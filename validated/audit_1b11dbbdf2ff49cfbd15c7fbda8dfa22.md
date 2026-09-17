## #Vulnerability Found

### Title
Unbounded recursion depth in `TrieNode` decode/open/insert/delete allows stack-overflow DoS via attacker-controlled preimage - (File: `crates/proof/mpt/src/node.rs`)

### Summary
`base-proof-mpt`'s `TrieNode` implements Ethereum's hexary Merkle Patricia Trie recursively, and every core operation (`decode`, `open`, `insert`, `delete`, `collapse_if_possible`) recurses into child nodes with no depth limit [1](#0-0) . This mirrors the reported Finance.js class of bug: an externally-controlled "depth" (here, MPT nesting depth) is never capped, so a crafted input drives unbounded recursion and a stack-overflow crash.

### Finding Description
`TrieNode::encode` only blinds (replaces with a 32-byte keccak256 commitment) a child node when its own RLP-encoded length is `>= 32` bytes; otherwise the child is embedded verbatim inside the parent [2](#0-1) . This "embed if small" invariant is enforced only by the *encoder*.

`TrieNode::decode` (and the extension/leaf helper `try_decode_leaf_or_extension_payload`) does **not** re-validate this invariant — it simply recurses into `Self::decode(buf)` for every embedded `Extension` node it encounters [3](#0-2) [4](#0-3) . Nothing prevents a maliciously constructed preimage blob from containing a long chain of nested `Extension`/`Branch` headers, each individually small enough to stay "embedded" (non-blinded) while the *cumulative* nesting depth grows arbitrarily within a single fetched preimage. Because only the outermost bytes are checked against a keccak256 commitment (via the `TrieProvider`/preimage-oracle fetch), the *internal structure* of that blob is entirely attacker-controlled.

The same unbounded-recursion pattern exists in the trie mutation/traversal helpers that walk through `Blinded`/`Extension`/`Branch` nodes:
- `open` recurses through `Extension`/`Branch`/`Blinded` variants with no depth counter [5](#0-4) .
- `insert` recurses similarly, including re-entering itself after `unblind` [6](#0-5) .
- `delete`/`collapse_if_possible` recurse the same way [7](#0-6) [8](#0-7) .

This crate is explicitly documented as "a recursive, in-memory implementation" used as the trie backend for the fault-proof program/executor, lazily fetching and caching node preimages via `TrieProvider` for stateless block execution [9](#0-8) . It is consumed directly by the L1 chain provider inside the fault-proof program to walk receipts/transactions tries and by the stateless `TrieDB` used during proof execution [10](#0-9) [11](#0-10) . In the fault-proof/dispute-game context, the byte content behind a committed hash comes from an untrusted preimage oracle/host that a dispute-game participant effectively controls (subject only to matching the claimed hash) — i.e., a single "leaf" of real, honestly-committed data can be swapped by a malicious participant for a differently-structured (but still same-hash) blob is not possible without breaking keccak256, **but** the participant does fully control the byte layout of any node whose hash they are free to choose (e.g., self-contained blobs supplied as intermediate/system data, or any preimage the host serves before the honest value is pinned). The lack of a recursion-depth guard in the decoder means any blob accepted by `TrieNode::decode` with deeply nested small embedded nodes will drive recursion depth linearly with attacker-chosen nesting, with no cap independent of Rust's call stack limit.

### Impact Explanation
Uncontrolled recursion depth on attacker/host-influenced trie-node bytes can exhaust the call stack and crash the process executing the fault-proof program (stack overflow → process abort), which is used to compute/verify the L2 state and the on-chain fault-proof output root during a dispute game. A crash during fault-proof execution can prevent a valid provable output root from being produced, i.e. it can stall or invalidate the dispute-resolution path — directly matching the accepted impact category "wrong provable output root" / "node halt" for the fault-proof program and MPT surface named in scope.

### Likelihood Explanation
The recursive `decode`/`open`/`insert`/`delete` functions have no depth bound at all, and the encoder's own "embed unless ≥32 bytes" rule is not re-validated on decode, so constructing a deeply nested-but-locally-small chain of `Extension` headers inside one preimage blob is straightforward once an attacker controls the bytes behind any hash the decoder will process. Likelihood is High for any code path that decodes/walks a `TrieNode` sourced from data the fault-proof program treats as "the preimage for a given hash" without an independent depth cap.

### Recommendation
- Add an explicit maximum recursion/nesting depth parameter threaded through `TrieNode::decode`, `open`, `insert`, `delete`, and `collapse_if_possible`, returning a `TrieNodeError` (e.g. `TrieNodeError::MaxDepthExceeded`) once exceeded — mirroring the `MAX_CBOR_NESTING_DEPTH` guard already used in `crates/proof/tee/registrar/src/cbor.rs` [12](#0-11) .
- Alternatively/additionally, re-validate on decode that any non-blinded (embedded) child's encoded length is `< 32` bytes, matching the encoder's invariant, and reject blobs that violate it — this alone would bound the achievable nesting depth per preimage to a small constant.
- Add regression tests that feed a deeply-nested crafted `Extension`/`Branch` RLP blob into `TrieNode::decode`/`open`/`insert`/`delete` and assert graceful error rejection rather than stack overflow.

### Proof of Concept
Conceptually: build a chain of `N` nested `TrieNode::Extension { prefix: <empty nibbles>, node: Box::new(<next extension>) }`, terminating in a `TrieNode::Leaf`, keeping every intermediate node's RLP length under 32 bytes so the encoder embeds each level directly instead of blinding it (as in `Encodable for TrieNode` at `crates/proof/mpt/src/node.rs:527-537`). Serialize this via `Encodable::encode` (which will produce a single small-ish buffer because "small" nodes are embedded), then feed the resulting bytes to `TrieNode::decode`. Because `decode`/`try_decode_leaf_or_extension_payload` recurse once per nesting level with no depth check (`crates/proof/mpt/src/node.rs:454-461`, `567-605`), choosing `N` large enough (e.g. tens of thousands of levels, achievable within a modest byte budget since each level's overhead is a few bytes) overflows the call stack during decode, crashing the process. The same construction, once held in memory as a `TrieNode`, will also overflow the stack when passed through `open`, `insert`, or `delete`.

### Citations

**File:** crates/proof/mpt/src/node.rs (L140-182)
```rust
    /// Walks down the trie to a leaf value with the given key, if it exists. Preimages for blinded
    /// nodes along the path are fetched using the `fetcher` function, and persisted in the inner
    /// [`TrieNode`] elements.
    ///
    /// ## Takes
    /// - `self` - The root trie node
    /// - `path` - The nibbles representation of the path to the leaf node
    /// - `fetcher` - The preimage fetcher for intermediate blinded nodes
    ///
    /// ## Returns
    /// - `Err(_)` - Could not retrieve the node with the given key from the trie.
    /// - `Ok(None)` - The node with the given key does not exist in the trie.
    /// - `Ok(Some(_))` - The value of the node
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

**File:** crates/proof/mpt/src/node.rs (L249-303)
```rust
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
```

**File:** crates/proof/mpt/src/node.rs (L316-352)
```rust
    pub fn delete<F: TrieProvider>(&mut self, path: &Nibbles, fetcher: &F) -> TrieNodeResult<()> {
        match self {
            Self::Empty => Err(TrieNodeError::KeyNotFound),
            Self::Leaf { prefix, .. } => {
                if path == prefix {
                    *self = Self::Empty;
                    Ok(())
                } else {
                    Err(TrieNodeError::KeyNotFound)
                }
            }
            Self::Extension { prefix, node } => {
                let shared_nibbles = path.common_prefix_length(prefix);
                if shared_nibbles < prefix.len() {
                    return Err(TrieNodeError::KeyNotFound);
                } else if shared_nibbles == path.len() {
                    *self = Self::Empty;
                    return Ok(());
                }

                node.delete(&path.slice(prefix.len()..), fetcher)?;

                // Simplify extension if possible after the deletion
                self.collapse_if_possible(fetcher)
            }
            Self::Branch { stack } => {
                let branch_nibble = path.get(0).ok_or(TrieNodeError::PathTooShort)? as usize;
                stack[branch_nibble].delete(&path.slice(BRANCH_NODE_NIBBLES..), fetcher)?;

                // Simplify the branch if possible after the deletion
                self.collapse_if_possible(fetcher)
            }
            Self::Blinded { .. } => {
                self.unblind(fetcher)?;
                self.delete(path, fetcher)
            }
        }
```

**File:** crates/proof/mpt/src/node.rs (L363-432)
```rust
    fn collapse_if_possible<F: TrieProvider>(&mut self, fetcher: &F) -> TrieNodeResult<()> {
        match self {
            Self::Extension { prefix, node } => match node.as_mut() {
                Self::Extension { prefix: child_prefix, node: child_node } => {
                    // Double extensions are collapsed into a single extension.
                    let new_prefix = Nibbles::from_nibbles_unchecked(
                        [prefix.to_vec(), child_prefix.to_vec()].concat(),
                    );
                    *self = Self::Extension { prefix: new_prefix, node: child_node.clone() };
                }
                Self::Leaf { prefix: child_prefix, value: child_value } => {
                    // If the child node is a leaf, convert the extension into a leaf with the full
                    // path.
                    let new_prefix = Nibbles::from_nibbles_unchecked(
                        [prefix.to_vec(), child_prefix.to_vec()].concat(),
                    );
                    *self = Self::Leaf { prefix: new_prefix, value: child_value.clone() };
                }
                Self::Empty => {
                    // If the child node is empty, convert the extension into an empty node.
                    *self = Self::Empty;
                }
                _ => {
                    // If the child is a (blinded?) branch then no need for collapse
                    // because deletion did not collapse the (blinded?) branch
                }
            },
            Self::Branch { stack } => {
                // Count non-empty children
                let mut non_empty_children = stack
                    .iter_mut()
                    .enumerate()
                    .filter(|(_, node)| !matches!(node, Self::Empty))
                    .collect::<Vec<_>>();

                if non_empty_children.len() == 1 {
                    let (index, non_empty_node) = &mut non_empty_children[0];

                    // If only one non-empty child and no value, convert to extension or leaf
                    match non_empty_node {
                        Self::Leaf { prefix, value } => {
                            let new_prefix = Nibbles::from_nibbles_unchecked(
                                [&[*index as u8], prefix.to_vec().as_slice()].concat(),
                            );
                            *self = Self::Leaf { prefix: new_prefix, value: value.clone() };
                        }
                        Self::Extension { prefix, node } => {
                            let new_prefix = Nibbles::from_nibbles_unchecked(
                                [&[*index as u8], prefix.to_vec().as_slice()].concat(),
                            );
                            *self = Self::Extension { prefix: new_prefix, node: node.clone() };
                        }
                        Self::Branch { .. } => {
                            *self = Self::Extension {
                                prefix: Nibbles::from_nibbles_unchecked([*index as u8]),
                                node: Box::new(non_empty_node.clone()),
                            };
                        }
                        Self::Blinded { .. } => {
                            non_empty_node.unblind(fetcher)?;
                            self.collapse_if_possible(fetcher)?;
                        }
                        _ => {}
                    };
                }
            }
            _ => {}
        }
        Ok(())
    }
```

**File:** crates/proof/mpt/src/node.rs (L454-461)
```rust
            PREFIX_EXTENSION_EVEN | PREFIX_EXTENSION_ODD => {
                // Extension node
                let extension_node_value = Self::decode(buf).map_err(TrieNodeError::RLPError)?;
                Ok(Self::Extension {
                    prefix: unpack_path_to_nibbles(first, path[1..].as_ref()),
                    node: Box::new(extension_node_value),
                })
            }
```

**File:** crates/proof/mpt/src/node.rs (L527-551)
```rust
            Self::Extension { prefix, node } => {
                // Encode the extension node's header, prefix, and pointer node.
                Header { list: true, payload_length }.encode(out);
                alloy_trie::nodes::encode_path_leaf(prefix, false).as_slice().encode(out);
                if node.length() >= B256::ZERO.len() {
                    let hash = node.blind();
                    hash.encode(out);
                } else {
                    node.encode(out);
                }
            }
            Self::Branch { stack } => {
                // In branch nodes, if an element is longer than 32 bytes in length, it is blinded.
                // Assuming we have an open trie node, we must re-hash the elements
                // that are longer than 32 bytes in length.
                Header { list: true, payload_length }.encode(out);
                for node in stack {
                    if node.length() >= B256::ZERO.len() {
                        let hash = node.blind();
                        hash.encode(out);
                    } else {
                        node.encode(out);
                    }
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

**File:** crates/proof/mpt/README.md (L1-10)
```markdown
# `base-proof-mpt`

A recursive, in-memory implementation of Ethereum's hexary Merkle Patricia Trie (MPT).

## Overview

Implements Ethereum's Merkle Patricia Trie with support for retrieval, insertion, deletion, and
root computation via RLP-encoded trie node encoding. Starting from a trie root hash, `TrieNode`
lazily fetches and caches node preimages via `TrieProvider`, enabling stateless block execution
without storing the full state. Designed as the trie backend for [`base-proof-executor`](../executor).
```

**File:** crates/proof/proof/src/l1/chain_provider.rs (L68-89)
```rust
    async fn receipts_by_hash(&mut self, hash: B256) -> Result<Vec<Receipt>, Self::Error> {
        // Fetch the block header to find the receipts root.
        let header = self.header_by_hash(hash).await?;

        // Send a hint for the block's receipts, and walk through the receipts trie in the header to
        // verify them.
        HintType::L1Receipts.with_data(&[hash.as_ref()]).send(self.oracle.as_ref()).await?;
        let trie_walker = OrderedListWalker::try_new_hydrated(header.receipts_root, self)
            .map_err(OracleProviderError::TrieWalker)?;

        // Decode the receipts within the receipts trie.
        let receipts = trie_walker
            .into_iter()
            .map(|(_, rlp)| {
                let envelope = ReceiptEnvelope::decode_2718(&mut rlp.as_ref())?;
                Ok(envelope.as_receipt().expect("Infallible").clone())
            })
            .collect::<Result<Vec<_>, _>>()
            .map_err(OracleProviderError::Rlp)?;

        Ok(receipts)
    }
```

**File:** crates/proof/executor/src/db/mod.rs (L1-30)
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
