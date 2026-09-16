### Title
Unbounded recursive RLP decoding of `TrieNode` allows stack overflow in the fault-proof program - ([File: crates/proof/mpt/src/node.rs])

### Summary
`TrieNode::decode` recursively descends into nested `Extension`/`Branch` sub-nodes with no depth limit. Because a single fetched preimage blob may itself encode arbitrarily deep inline (non-blinded) nesting, a fault-proof-program participant who controls the byte content behind a keccak-verified preimage hash can trigger unbounded recursion and a stack overflow in a single decode call, matching the CVE-2025-9714 bug class (recursive descent parser without depth tracking).

### Finding Description
`TrieNode` is a recursive enum decoded via `alloy_rlp::Decodable`: [1](#0-0) 

- If the RLP item is a 17-element list, it decodes a `Branch` by calling `Vec::<Self>::decode`, which invokes `TrieNode::decode` recursively for every one of the (up to) 17 stack slots.
- If it is a 2-element list, `try_decode_leaf_or_extension_payload` is called: [2](#0-1) 

which, for extension-prefixed paths, calls `Self::decode(buf)` again on the child node inline in the same buffer.

Only nodes that are **more than 32 bytes long** are "blinded" (replaced with a 32-byte hash reference) during *encoding*: [3](#0-2) 

There is no explicit recursion-depth counter threaded through `decode`/`try_decode_leaf_or_extension_payload`, unlike other parsers in the same codebase that do bound recursion, e.g. the CBOR parser used for TEE attestation which explicitly tracks and rejects excess depth: [4](#0-3) 

Contrast this with `TrieNode::decode`, which has no equivalent `depth` parameter or `MAX_*_NESTING_DEPTH` check.

`TrieNode::decode` is the sole hook the fault-proof program uses to interpret every preimage fetched from the (attacker-influenceable) preimage oracle on both L1 and L2 chain providers: [5](#0-4) [6](#0-5) 

and also from the on-the-fly `TrieDB` used during stateless block execution inside the fault-proof program: [7](#0-6) 

Each preimage is only checked against its keccak256 hash — the *content* between the RLP list-header boundaries is otherwise unconstrained by the requester. Because a single blob can contain many small (≤32-byte) sub-lists that are all encoded *inline* (not blinded, since blinding is size-triggered, not depth-triggered), an attacker who controls the bytes behind one hash reference (e.g. a dispute-game participant supplying state/witness data, or a malicious/compromised preimage source feeding the host) can construct one preimage that itself contains deeply nested `Extension`/`Branch` structures, forcing `TrieNode::decode` to recurse thousands of times purely off a single fetched blob, with no depth cap to reject it.

### Impact Explanation
A stack overflow during `TrieNode::decode` inside the fault-proof program crashes the process executing the derivation/execution trace. Since the fault-proof program is what computes and asserts the claimed L2 output root during a dispute game, a party able to influence the witness/preimage data supplied to a challenged claim could cause the honest verifier's program to crash instead of producing a correct output root — potentially preventing correct dispute resolution (wrong/failed provable output root, node halt of the verifying program) for the affected claim.

### Likelihood Explanation
Reaching this path requires being able to influence bytes behind a hash the fault-proof program will decode as a trie node — this is plausible for a dispute-game participant supplying disputed L2/L1 state data, or through any preimage source that isn't perfectly trusted, since the only integrity check is the keccak256 hash match, not structural bound on nesting. No additional privilege beyond normal dispute-game participation/witness-data provisioning is required.

### Recommendation
Add an explicit recursion-depth (or node-count) bound to `TrieNode::decode` / `try_decode_leaf_or_extension_payload` / `Vec::<Self>::decode` for branches, mirroring the `MAX_CBOR_NESTING_DEPTH` pattern already used in `crates/proof/tee/registrar/src/cbor.rs`, and reject preimages that exceed the maximum possible MPT depth (bounded by nibble-path length, i.e. ~64 for 32-byte keys).

### Proof of Concept
Construct a single RLP blob whose top-level item is a `Branch`'s single non-empty stack slot pointing to another inline `Extension`/`Branch`, repeated thousands of times within one buffer (never crossing the 32-byte blinding threshold at any single level, so each sub-node stays inline rather than being blinded into a separate hash reference). Store this blob under the keccak256 hash the program expects via the (attacker-influenced) preimage source, then trigger any code path that calls `TrieProvider::trie_node_by_hash` on that hash (e.g. `OracleL1ChainProvider::trie_node_by_hash` / `OracleL2ChainProvider::trie_node_by_hash`); `TrieNode::decode` recurses once per nested level with no depth check, exhausting the stack.

I was not able to execute this PoC or confirm the exact maximum inline-nesting bytes-per-recursion-level ratio (i.e., precisely how many recursion levels fit in a single preimage of the size limits enforced elsewhere in the preimage/oracle pipeline), since I don't have access to run code in this environment — this would need to be validated by an engineer with the ability to build and run the `base-proof-mpt` crate.

### Citations

**File:** crates/proof/mpt/src/node.rs (L57-61)
```rust
/// The [`alloy_rlp::Encodable`] and [`alloy_rlp::Decodable`] traits are implemented for
/// [`TrieNode`], allowing for RLP encoding and decoding of the types for storage and retrieval. The
/// implementation of these traits will implicitly blind nodes that are longer than 32 bytes in
/// length when encoding. When decoding, the implementation will leave blinded nodes in place.
///
```

**File:** crates/proof/mpt/src/node.rs (L439-469)
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
            PREFIX_LEAF_EVEN | PREFIX_LEAF_ODD => {
                // Leaf node
                let value = Bytes::decode(buf).map_err(TrieNodeError::RLPError)?;
                Ok(Self::Leaf { prefix: unpack_path_to_nibbles(first, path[1..].as_ref()), value })
            }
            _ => Err(TrieNodeError::InvalidNodeType),
        }
    }
```

**File:** crates/proof/mpt/src/node.rs (L567-605)
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
}
```

**File:** crates/proof/tee/registrar/src/cbor.rs (L94-100)
```rust
    /// Decodes an item with `depth` enclosing containers already entered.
    pub fn read_at(bytes: &[u8], start: usize, depth: usize) -> PlannerResult<Self> {
        if depth > MAX_CBOR_NESTING_DEPTH {
            return Err(PlannerError::Cose(format!(
                "CBOR nesting exceeds maximum depth {MAX_CBOR_NESTING_DEPTH}"
            )));
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

**File:** crates/proof/proof/src/l2/chain_provider.rs (L173-190)
```rust
impl<T: CommsClient> TrieProvider for OracleL2ChainProvider<T> {
    type Error = OracleProviderError;

    fn trie_node_by_hash(&self, key: B256) -> Result<TrieNode, OracleProviderError> {
        // On L2, trie node preimages are stored as keccak preimage types in the oracle. We assume
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

**File:** crates/proof/executor/src/db/mod.rs (L1-18)
```rust
//! This module contains an implementation of an in-memory Trie DB for [`revm`], that allows for
//! incremental updates through fetching node preimages on the fly during execution.

use alloc::{string::ToString, vec::Vec};

use alloy_consensus::{EMPTY_ROOT_HASH, Header, Sealed};
use alloy_primitives::{Address, B256, U256, keccak256};
use alloy_rlp::{Decodable, Encodable};
use alloy_trie::{Nibbles, TrieAccount};
use base_proof_mpt::{TrieHinter, TrieNode, TrieNodeError};
use revm::{
    Database,
    database::{BundleState, states::StorageSlot},
    primitives::{BLOCK_HASH_HISTORY, HashMap},
    state::{AccountInfo, Bytecode},
};

use crate::errors::{TrieDBError, TrieDBResult};
```
