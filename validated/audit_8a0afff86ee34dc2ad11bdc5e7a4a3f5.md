### Title
Unbounded recursion in `TrieNode::decode` allows stack-exhaustion DoS of the fault-proof program via a malicious MPT node preimage - (File: crates/proof/mpt/src/node.rs)

### Summary
`TrieNode::decode` (and its helper `try_decode_leaf_or_extension_payload`) recursively decode RLP-encoded MPT nodes with no depth limit. Because the recursion happens on unvalidated bytes returned by the preimage oracle used by the Base fault-proof program, a dispute-game participant that controls (or spoofs) preimage data can craft a single blob containing thousands of nested `Extension` node headers, causing native stack exhaustion and crashing the fault-proof client/host process — directly analogous to CVE-2017-9766's unbounded-recursion stack exhaustion in Wireshark's PROFINET dissector.

### Finding Description
`TrieNode::decode` peeks at the RLP header and, for a 2-element list, calls `Self::try_decode_leaf_or_extension_payload`: [1](#0-0) 

Inside that helper, when the decoded path indicates an `Extension` node, the inner child node is decoded by directly recursing into `Self::decode(buf)` with no depth counter or bound: [2](#0-1) 

In the canonical MPT encoding produced by an honest encoder, any node larger than 32 bytes is "blinded" into a bare keccak256 commitment, which naturally caps nesting depth in a single buffer at a small number of levels (`blind()`/`blinded_length()`): [3](#0-2) 

However, this blinding is a property of the *encoder*, not enforced by the *decoder*. `TrieNode::decode` will happily accept and recurse through an arbitrarily deep chain of unblinded, inline `Extension` headers packed into one buffer, since no code path checks recursion depth or total node count during decode.

This decode path is directly reachable from attacker/adversary-influenced data in the fault-proof program. `OracleL1ChainProvider::trie_node_by_hash` fetches a preimage from the oracle keyed by a keccak256 commitment and immediately calls `TrieNode::decode` on the raw bytes: [4](#0-3) 

The preimage-key verification only guarantees that the returned bytes hash to the requested commitment — it does not constrain the *content or structure* of those bytes. An adversarial host/oracle-server (which, in the fault-proof/dispute-game model, is explicitly an untrusted party a dispute participant can run) can trivially compute `keccak256(payload)` for any crafted `payload` it wants (e.g., a chain of nested `Extension` list headers) and serve it as a valid preimage for that hash. The same recursive-decode primitive is also used to walk ordered lists (receipts/transactions tries) via `OrderedListWalker`, widening the reachable surface: [5](#0-4) 

No `recursion_limit`, iteration cap, or node-count/depth bound exists anywhere in `crates/proof/mpt/src/node.rs` to defend against this.

### Impact Explanation
A crafted deeply-nested trie-node preimage causes the fault-proof program (and the offline/host witness generation path in `crates/proof/host`) to blow its call stack and abort/crash (stack exhaustion → process termination), rather than returning a normal decode error. In the context of the Base dispute-game / fault-proof system, this is a node-halt class issue: it can prevent an honest party from generating a valid proof/witness for a dispute step whose derivation touches the poisoned preimage, or crash the proving/host infrastructure that processes untrusted L1 data during derivation. This matches the "node halt" / "wrong provable output root" impact categories in scope, since a crashed fault-proof program cannot produce a correct output root and can be leveraged to stall or corrupt the dispute resolution process.

### Likelihood Explanation
Likelihood is high for any party that can supply preimage data consumed by `trie_node_by_hash` — including a malicious/faulty host implementation, a compromised preimage server, or (depending on deployment) a dispute participant influencing which preimages are served during proof execution. Constructing the malicious payload requires only computing a keccak256 hash of an attacker-chosen byte string (trivial, no cryptographic break needed), and the decode path is exercised on every L1/L2 trie node lookup during derivation, receipts/transactions verification, and state execution.

### Recommendation
Add an explicit maximum recursion/nesting depth (and/or convert the recursive `TrieNode::decode`/`try_decode_leaf_or_extension_payload` into an iterative, depth-tracked decoder) so that decoding aborts with a normal `alloy_rlp::Error` once a bounded depth (e.g., matching the maximum possible unblinded nesting for legitimate 32-byte-blinding rules) is exceeded, rather than recursing natively. Apply the same bound to `open`, `insert`, `delete`, and `collapse_if_possible`, which share the same unbounded-recursion pattern over attacker-influenced blinded/unblinded node chains.

### Proof of Concept
1. Construct a byte buffer `payload` consisting of N (e.g., 100,000) nested RLP list headers, each encoding a 2-element `Extension`-shaped node (`[encoded_path, child]`), where the innermost element is a valid short leaf, i.e. `rlp([path_1, rlp([path_2, rlp([path_3, ... rlp([path_N, leaf_value])])])])`, with no element individually exceeding 32 bytes so it is not required to be pre-blinded.
2. Compute `key = keccak256(payload)`.
3. Serve `payload` as the response to `oracle.get(PreimageKey::new(key, PreimageKeyType::Keccak256))` for a trie-node lookup reached during L1/L2 derivation (e.g., by controlling the host/oracle backend in `crates/proof/host` or a test harness driving `OracleL1ChainProvider::trie_node_by_hash`).
4. Trigger derivation so `TrieNode::decode(&mut payload.as_ref())` is invoked; the recursive descent through `try_decode_leaf_or_extension_payload` → `Self::decode` will recurse N times, exhausting the stack and crashing the process before any depth/size sanity check is applied. [2](#0-1) [6](#0-5)

### Citations

**File:** crates/proof/mpt/src/node.rs (L110-122)
```rust
    /// Blinds the [`TrieNode`].. Alternatively, if the [`TrieNode`] is a [`TrieNode::Blinded`] node
    /// already, its commitment is returned directly.
    pub fn blind(&self) -> B256 {
        match self {
            Self::Blinded { commitment } => *commitment,
            Self::Empty => EMPTY_ROOT_HASH,
            _ => {
                let mut rlp_buf = Vec::with_capacity(self.length());
                self.encode(&mut rlp_buf);
                keccak256(rlp_buf)
            }
        }
    }
```

**File:** crates/proof/mpt/src/node.rs (L439-461)
```rust
    fn try_decode_leaf_or_extension_payload(buf: &mut &[u8]) -> TrieNodeResult<Self> {
        // Decode the path and value of the leaf or extension node.
        let path = Bytes::decode(buf).map_err(TrieNodeError::RLPError)?;
        let Some(first_byte) = path.first() else {
            return Err(TrieNodeError::InvalidNodeType);
        };
        let first_nibble = first_byte >> NIBBLE_WIDTH;
        let first = match first_nibble {
            PREFIX_EXTENSION_ODD | PREFIX_LEAF_ODD => Some(first_byte & 0x0F),
            PREFIX_EXTENSION_EVEN | PREFIX_LEAF_EVEN => None,
            _ => return Err(TrieNodeError::InvalidNodeType),
        };

        // Check the high-order nibble of the path to determine the type of node.
        match first_nibble {
            PREFIX_EXTENSION_EVEN | PREFIX_EXTENSION_ODD => {
                // Extension node
                let extension_node_value = Self::decode(buf).map_err(TrieNodeError::RLPError)?;
                Ok(Self::Extension {
                    prefix: unpack_path_to_nibbles(first, path[1..].as_ref()),
                    node: Box::new(extension_node_value),
                })
            }
```

**File:** crates/proof/mpt/src/node.rs (L567-604)
```rust
impl Decodable for TrieNode {
    /// Attempts to decode the [`TrieNode`].
    fn decode(buf: &mut &[u8]) -> alloy_rlp::Result<Self> {
        // Peek at the header to determine the type of Trie node we're currently decoding.
        let header = Header::decode(&mut (**buf).as_ref())?;

        if header.list {
            // Peek at the RLP stream to determine the number of elements in the list.
            let list_length = rlp_list_element_length(&mut (**buf).as_ref())?;

            match list_length {
                BRANCH_LIST_LENGTH => {
                    let list = Vec::<Self>::decode(buf)?;
                    Ok(Self::Branch { stack: list })
                }
                LEAF_OR_EXTENSION_LIST_LENGTH => {
                    // Advance the buffer to the start of the list payload.
                    buf.advance(header.length());
                    // Decode the leaf or extension node's raw payload.
                    Self::try_decode_leaf_or_extension_payload(buf)
                        .map_err(|_| alloy_rlp::Error::UnexpectedList)
                }
                _ => Err(alloy_rlp::Error::UnexpectedLength),
            }
        } else {
            match header.payload_length {
                0 => {
                    buf.advance(header.length());
                    Ok(Self::Empty)
                }
                32 => {
                    let commitment = B256::decode(buf)?;
                    Ok(Self::new_blinded(commitment))
                }
                _ => Err(alloy_rlp::Error::UnexpectedLength),
            }
        }
    }
```

**File:** crates/proof/proof/src/l1/chain_provider.rs (L125-142)
```rust
impl<T: CommsClient> TrieProvider for OracleL1ChainProvider<T> {
    type Error = OracleProviderError;

    fn trie_node_by_hash(&self, key: B256) -> Result<TrieNode, Self::Error> {
        // On L1, trie node preimages are stored as keccak preimage types in the oracle. We assume
        // that a hint for these preimages has already been sent, prior to this call.
        crate::block_on(async move {
            TrieNode::decode(
                &mut self
                    .oracle
                    .get(PreimageKey::new(*key, PreimageKeyType::Keccak256))
                    .await
                    .map_err(OracleProviderError::Preimage)?
                    .as_ref(),
            )
            .map_err(OracleProviderError::Rlp)
        })
    }
```

**File:** crates/proof/mpt/src/list_walker.rs (L1-13)
```rust
//! This module contains the [`OrderedListWalker`] struct, which allows for traversing an MPT root
//! of a derivable ordered list.

use alloc::{collections::VecDeque, string::ToString, vec};
use core::marker::PhantomData;

use alloy_primitives::{B256, Bytes};
use alloy_rlp::EMPTY_STRING_CODE;

use crate::{
    TrieNode, TrieNodeError, TrieProvider,
    errors::{OrderedListWalkerError, OrderedListWalkerResult},
};
```
