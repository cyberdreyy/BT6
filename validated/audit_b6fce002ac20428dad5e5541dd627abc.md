I have enough evidence now to write up the finding.

### Title
Unbounded recursive RLP decoding in `TrieNode::decode` allows attacker-crafted preimage to stack-overflow the fault-proof program - ([File: crates/proof/mpt/src/node.rs])

### Summary
`base-proof-mpt`'s `TrieNode::decode` (and the related `open`/`insert`/`delete`/`collapse_if_possible` methods) recursively call themselves/`Self::decode` to unwrap nested `Extension`/`Branch`/`Leaf` structures without any depth limit. A malicious L1 or L2 trie-node preimage (fetched via the preimage oracle by hash during fault-proof execution) can nest thousands of `Extension`/`Branch` levels inline within a single blob — since only individual sub-nodes whose RLP encoding exceeds 32 bytes must be "blinded" into a separate hash-addressed preimage, small nested structures can be embedded directly and decoded in one recursive call chain — causing uncontrolled native-stack growth and a crash (stack overflow) analogous to the iccDEV `SIccCalcOp::ArgsUsed()` SO bug (CVE-2026-34536), which is also unbounded recursion triggered by a maliciously crafted color profile.

### Finding Description
`TrieNode::decode` recursively parses RLP data: for a list header with `LEAF_OR_EXTENSION_LIST_LENGTH` (2) it calls `try_decode_leaf_or_extension_payload`, which for an extension-type node recursively invokes `Self::decode(buf)` again on the embedded child node [1](#0-0) . For a `BRANCH_LIST_LENGTH` (17) list it calls `Vec::<Self>::decode(buf)`, which itself recursively decodes each of the 17 child elements via the same `Decodable` impl [2](#0-1) . None of these code paths track or bound recursion depth.

The only constraint imposed on non-list nodes is that a bare "string" item must be either empty (0 bytes → `Empty`) or exactly 32 bytes (→ `Blinded` commitment); anything else errors [3](#0-2) . This constraint applies only to single hash-pointer nodes, not to nested list structures embedded directly inside a parent list. Because RLP lists can be nested arbitrarily inside a single buffer without ever needing a separate keccak-addressed preimage (that requirement is only enforced by the *encoder*'s `blind()` heuristic when producing canonical output, not by the decoder), an attacker who controls a preimage blob can build one small buffer containing thousands of nested `Extension`-of-`Extension`-of-... structures, all consumed in a single `TrieNode::decode` call with proportional native call-stack depth.

This preimage is supplied to the fault-proof/stateless execution program through `TrieProvider::trie_node_by_hash`, which fetches attacker/challenger-controlled bytes from the preimage oracle and immediately calls `TrieNode::decode` on them, e.g. in `OracleL1ChainProvider::trie_node_by_hash` and `OracleL2ChainProvider::trie_node_by_hash` [4](#0-3) [5](#0-4) , and in the stateless `TrieDB`/`StatelessL2Builder` execution path used for fault-proof block replay [6](#0-5) . The preimage oracle only verifies that `keccak256(value) == key` for `Keccak256`-typed keys [7](#0-6)  — it does not, and cannot, validate the *internal structure* of the value, so a hash-valid but maliciously nested blob passes preimage verification and reaches `TrieNode::decode` unmodified.

The same unbounded recursion pattern also exists in `TrieNode::open`, `insert`, `delete`, and `collapse_if_possible`, which recurse through `Extension`/`Branch`/`Blinded` variants without depth limits [8](#0-7) [9](#0-8) , but the `decode` path is the most directly attacker-reachable since it operates on a single externally-supplied buffer with no traversal-length restriction from a legitimate proof shape.

### Impact Explanation
This is squarely in the fault-proof program / MPT surface explicitly called out as in-scope. A crafted trie-node preimage causing a stack overflow during fault-proof execution (in the FPVM, TEE enclave, or zkVM client) would crash/halt the verifying program, preventing it from computing a correct output root during dispute resolution — this can manifest as a node halt of the proof-generation process or a wrong/failed provable output, undermining the fault-proof system's ability to settle disputes correctly. Depending on runtime (RISC-V/MIPS emulated FPVM, native host, zkVM), a stack overflow can range from a clean panic to undefined behavior in constrained execution environments.

### Likelihood Explanation
The vulnerable code decodes arbitrary externally-supplied bytes with no depth bound; any party that can supply/serve a preimage during dispute-game or oracle-backed execution (e.g., a disputing party feeding a hint/preimage, or an execution witness for stateless block building) can trigger it. No signature or special privilege is required beyond controlling the preimage bytes returned for a given hash pointer.

### Recommendation
Add an explicit recursion/depth counter (or convert `TrieNode::decode`/`open`/`insert`/`delete`/`collapse_if_possible` to iterative implementations with an explicit work stack) and reject nodes whose embedded nesting exceeds a small, protocol-reasonable bound (e.g., matching the maximum realistic MPT extension/branch chain length, ~64–128). Enforce this bound specifically in the decode path since that is the entry point for untrusted preimages.

### Proof of Concept
Construct nested RLP: build the innermost `Leaf` node (`rlp([encoded_path, value])`), then wrap it thousands of times in `Extension` nodes (`rlp([encoded_path, inner_node])`) each keeping the encoded length under 32 bytes so no blinding boundary is required in a decodable stream. Feed the final blob as the return value of `trie_node_by_hash`/preimage-oracle `get` for the corresponding hash and call `TrieNode::decode(&mut blob.as_ref())`; observe recursive stack growth proportional to nesting depth, eventually overflowing the native stack — mirroring `crates/utilities/cli/tests/sigsegv_test.rs`'s own `recurse()`-based stack-overflow harness pattern [10](#0-9)  but reached through legitimate `TrieNode::decode` recursion instead of synthetic test code.

### Citations

**File:** crates/proof/mpt/src/node.rs (L153-182)
```rust
    pub fn open<'a, F: TrieProvider>(
        &'a mut self,
        path: &Nibbles,
        fetcher: &F,
    ) -> TrieNodeResult<Option<&'a mut Bytes>> {
        match self {
            Self::Branch { stack } => {
                let branch_nibble = path.get(0).ok_or(TrieNodeError::PathTooShort)? as usize;
                stack
                    .get_mut(branch_nibble)
                    .map(|node| node.open(&path.slice(BRANCH_NODE_NIBBLES..), fetcher))
                    .unwrap_or(Ok(None))
            }
            Self::Leaf { prefix, value } => Ok((path == prefix).then_some(value)),
            Self::Extension { prefix, node } => {
                if path.slice(..prefix.len()) == *prefix {
                    // Follow extension branch
                    node.unblind(fetcher)?;
                    node.open(&path.slice(prefix.len()..), fetcher)
                } else {
                    Ok(None)
                }
            }
            Self::Blinded { .. } => {
                self.unblind(fetcher)?;
                self.open(path, fetcher)
            }
            Self::Empty => Ok(None),
        }
    }
```

**File:** crates/proof/mpt/src/node.rs (L195-305)
```rust
    pub fn insert<F: TrieProvider>(
        &mut self,
        path: &Nibbles,
        value: Bytes,
        fetcher: &F,
    ) -> TrieNodeResult<()> {
        match self {
            Self::Empty => {
                // If the trie node is null, insert the leaf node at the current path.
                *self = Self::Leaf { prefix: *path, value };
                Ok(())
            }
            Self::Leaf { prefix, value: leaf_value } => {
                let shared_extension_nibbles = path.common_prefix_length(prefix);

                // If all nibbles are shared, update the leaf node with the new value.
                if path == prefix {
                    *self = Self::Leaf { prefix: *prefix, value };
                    return Ok(());
                }

                // Create a branch node stack containing the leaf node and the new value.
                let mut stack = vec![Self::Empty; BRANCH_LIST_LENGTH];

                // Insert the shortened extension into the branch stack.
                let extension_nibble =
                    prefix.get(shared_extension_nibbles).ok_or(TrieNodeError::PathTooShort)?
                        as usize;
                stack[extension_nibble] = Self::Leaf {
                    prefix: prefix.slice(shared_extension_nibbles + BRANCH_NODE_NIBBLES..),
                    value: leaf_value.clone(),
                };

                // Insert the new value into the branch stack.
                let branch_nibble_new =
                    path.get(shared_extension_nibbles).ok_or(TrieNodeError::PathTooShort)? as usize;
                stack[branch_nibble_new] = Self::Leaf {
                    prefix: path.slice(shared_extension_nibbles + BRANCH_NODE_NIBBLES..),
                    value,
                };

                // Replace the leaf node with the branch if no nibbles are shared, else create an
                // extension.
                if shared_extension_nibbles == 0 {
                    *self = Self::Branch { stack };
                } else {
                    let raw_ext_nibbles = path.slice(..shared_extension_nibbles);
                    *self = Self::Extension {
                        prefix: raw_ext_nibbles,
                        node: Box::new(Self::Branch { stack }),
                    };
                }
                Ok(())
            }
            Self::Extension { prefix, node } => {
                let shared_extension_nibbles = path.common_prefix_length(prefix);
                if shared_extension_nibbles == prefix.len() {
                    node.insert(&path.slice(shared_extension_nibbles..), value, fetcher)?;
                    return Ok(());
                }

                // Create a branch node stack containing the leaf node and the new value.
                let mut stack = vec![Self::Empty; BRANCH_LIST_LENGTH];

                // Insert the shortened extension into the branch stack.
                let extension_nibble =
                    prefix.get(shared_extension_nibbles).ok_or(TrieNodeError::PathTooShort)?
                        as usize;
                let new_prefix = prefix.slice(shared_extension_nibbles + BRANCH_NODE_NIBBLES..);
                stack[extension_nibble] = if new_prefix.is_empty() {
                    // In the case that the extension node no longer has a prefix, insert the node
                    // verbatim into the branch.
                    node.as_ref().clone()
                } else {
                    Self::Extension { prefix: new_prefix, node: node.clone() }
                };

                // Insert the new value into the branch stack.
                let branch_nibble_new =
                    path.get(shared_extension_nibbles).ok_or(TrieNodeError::PathTooShort)? as usize;
                stack[branch_nibble_new] = Self::Leaf {
                    prefix: path.slice(shared_extension_nibbles + BRANCH_NODE_NIBBLES..),
                    value,
                };

                // Replace the extension node with the branch if no nibbles are shared, else create
                // an extension.
                if shared_extension_nibbles == 0 {
                    *self = Self::Branch { stack };
                } else {
                    let extension = path.slice(..shared_extension_nibbles);
                    *self = Self::Extension {
                        prefix: extension,
                        node: Box::new(Self::Branch { stack }),
                    };
                }
                Ok(())
            }
            Self::Branch { stack } => {
                // Follow the branch node to the next node in the path.
                let branch_nibble = path.get(0).ok_or(TrieNodeError::PathTooShort)? as usize;
                stack[branch_nibble].insert(&path.slice(BRANCH_NODE_NIBBLES..), value, fetcher)
            }
            Self::Blinded { .. } => {
                // If a blinded node is approached, reveal the node and continue the insertion
                // recursion.
                self.unblind(fetcher)?;
                self.insert(path, value, fetcher)
            }
        }
    }
```

**File:** crates/proof/mpt/src/node.rs (L453-461)
```rust
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

**File:** crates/proof/mpt/src/node.rs (L577-590)
```rust
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
```

**File:** crates/proof/mpt/src/node.rs (L591-603)
```rust
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

**File:** crates/proof/proof/src/l2/chain_provider.rs (L173-191)
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
}
```

**File:** crates/proof/executor/src/db/mod.rs (L1-35)
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

mod traits;
pub use traits::{NoopTrieDBProvider, TrieDBProvider};

/// A Trie DB that caches open state in-memory.
///
/// When accounts that don't already exist within the cached [`TrieNode`] are queried, the database
/// fetches the preimages of the trie nodes on the path to the account using the `PreimageFetcher`
/// (`F` generic). This allows for data to be fetched in a verifiable manner given an initial
/// trusted state root as it is needed during execution.
///
/// The [`TrieDB`] is intended to be wrapped by a [`State`], which is then used by [`revm`] to
/// capture state transitions during block execution.
///
/// [`State`]: revm::database::State
#[derive(Debug, Clone)]
pub struct TrieDB<F, H>
```

**File:** crates/proof/preimage/src/key.rs (L20-26)
```rust
    /// Commonly these local keys are mapped to bootstrap data for the fault proof program.
    Local = 1,
    /// Keccak256 key types are global and context independent. Preimages are mapped from the
    /// low-order 31 bytes of the preimage's `keccak256` digest to the preimage itself.
    #[default]
    Keccak256 = 2,
    /// `GlobalGeneric` key types are reserved for future use.
```

**File:** crates/utilities/cli/tests/sigsegv_test.rs (L43-55)
```rust
        "stack_overflow" => {
            // Infinite recursion to overflow the stack and hit the guard page.
            #[inline(never)]
            #[allow(unconditional_recursion)]
            fn recurse(n: u64) -> u64 {
                // Allocate stack space to accelerate overflow and prevent tail-call
                // optimization.
                let buf = [n; 64];
                recurse(black_box(buf[0].wrapping_add(1)))
            }
            let _ = recurse(black_box(0));
            unreachable!();
        }
```
