## Finding

### Title
Unbounded recursive RLP decoding of `TrieNode` allows dispute-game participants to crash the fault-proof program via stack exhaustion - (File: `crates/proof/mpt/src/node.rs`)

### Summary
The `base-proof-mpt` crate's `TrieNode::decode` implementation recursively decodes nested `Extension` nodes inline, with no depth counter or recursion limit, mirroring the exact bug class described in the Exiv2 advisory (`QuickTimeVideo::multipleEntriesDecoder`, CVE-2024-25112): an attacker-controlled, deeply nested encoding drives unbounded recursion and crashes the process by exhausting the call stack.

### Finding Description
`TrieNode::decode` peeks at the RLP header and, for a two-element list matching `LEAF_OR_EXTENSION_LIST_LENGTH`, calls `try_decode_leaf_or_extension_payload`, which — for extension-prefixed paths — calls `Self::decode(buf)` again on the immediately following bytes in the *same buffer*: [1](#0-0) [2](#0-1) 

Nothing in this decode path tracks or bounds recursion depth — unlike other decoders in the same codebase that explicitly guard against this exact class of bug, e.g. `CborItem::read_at` in the TEE registrar, which threads a `depth` parameter and rejects input once `depth > MAX_CBOR_NESTING_DEPTH`: [3](#0-2) 

By contrast, `TrieNode::open`, `TrieNode::unblind`, `TrieNode::collapse_if_possible`, `TrieNode::encode`/`length`/`payload_length` are all naturally recursive over the tree with no depth ceiling either: [4](#0-3) 

This `TrieNode` implementation is the trie backend used by the Base fault-proof program to walk L1 and L2 state/tx/receipt tries from preimage-oracle-supplied node bytes (`TrieProvider::trie_node_by_hash`), as consumed by `OracleL1ChainProvider` and `OracleL2ChainProvider`: [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7) 

The fault-proof/preimage-oracle protocol only guarantees that a preimage's `keccak256` matches its requested key — it places no constraint on the internal RLP structure of that preimage beyond what `TrieNode::decode` itself enforces. Because the recursive extension-chain decoding happens fully *inline within a single preimage blob* (not once per hash-verified fetch), a dispute-game participant can craft one small preimage blob containing thousands of nested minimal `Extension`-node RLP headers. Feeding this single blob through `trie_node_by_hash` → `TrieNode::decode` triggers thousands of stack frames in one synchronous call, without ever needing to satisfy additional hash checks per nesting level.

### Impact Explanation
A malicious dispute-game participant (challenger or defender) who controls the L1/L2 witness data supplied to the fault-proof program (via account/storage/tx/receipt trie node preimages) can trigger unbounded recursion in `TrieNode::decode`/`open`, exhausting the stack and crashing the proof program mid-execution. This halts the fault-proof program before it can compute or validate the claimed output root, denying honest participants the ability to resolve the dispute game correctly and potentially stalling or biasing dispute resolution — a node/service halt directly within the scope of "the fault-proof program and MPT."

### Likelihood Explanation
Likelihood is high for any actor who can supply an alternative branch of state/tx/receipt data during derivation or trie-node fetch in a dispute game (a core, expected capability of dispute-game participants supplying L1-derived data). The recursive call requires no special preconditions beyond providing a single small malformed preimage blob; no additional cryptographic requirement bounds the recursion.

### Recommendation
Add an explicit recursion-depth bound to `TrieNode::decode` (and, ideally, to `open`/`unblind`/`collapse_if_possible`/`encode`/`length`), analogous to `MAX_CBOR_NESTING_DEPTH` used elsewhere in this codebase (`crates/proof/tee/registrar/src/cbor.rs`). Thread a `depth: usize` parameter through the recursive decode calls, and reject/​error once depth exceeds the maximum theoretically valid MPT depth (bounded by the fixed key length, e.g. 64 nibbles for 32-byte keccak keys), converting a potential stack-overflow crash into a clean decode error.

### Proof of Concept
Construct a single RLP buffer `B` consisting of `N` (e.g. 50,000) nested two-element lists, each encoding an `Extension`-node prefix byte (`PREFIX_EXTENSION_EVEN`/`PREFIX_EXTENSION_ODD`) followed immediately by the next nested list, terminated by a trivial leaf/blinded node. Compute `key = keccak256(B)` and submit `B` as the preimage for `key` through the fault-proof preimage-oracle protocol (e.g. as one of the account/storage proof nodes hinted via `HintType::L2AccountStorageProof`, see `crates/proof/host/src/handler.rs`). When the client program calls `TrieProvider::trie_node_by_hash(key)` and the returned bytes are passed to `TrieNode::decode`, the recursive call to `Self::decode(buf)` inside `try_decode_leaf_or_extension_payload` will recurse `N` times on the single-threaded FPVM/host stack, exhausting it and crashing the process before the fault-proof program can produce a verdict.

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

**File:** crates/proof/mpt/src/node.rs (L452-469)
```rust
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

**File:** crates/proof/mpt/src/traits.rs (L11-25)
```rust
/// The [`TrieProvider`] trait defines the synchronous interface for fetching trie node preimages.
pub trait TrieProvider {
    /// The error type for fetching trie node preimages.
    type Error: Display;

    /// Fetches the preimage for the given trie node hash.
    ///
    /// ## Takes
    /// - `key`: The key of the trie node to fetch.
    ///
    /// ## Returns
    /// - Ok(TrieNode): The trie node preimage.
    /// - `Err(Self::Error)`: If the trie node preimage could not be fetched.
    fn trie_node_by_hash(&self, key: B256) -> Result<TrieNode, Self::Error>;
}
```

**File:** crates/proof/proof/src/l1/chain_provider.rs (L1-24)
```rust
//! Contains the concrete implementation of the [`ChainProvider`] trait for the proof.

use alloc::{boxed::Box, sync::Arc, vec::Vec};

use alloy_consensus::{Header, Receipt, ReceiptEnvelope, TxEnvelope};
use alloy_eips::eip2718::Decodable2718;
use alloy_primitives::B256;
use alloy_rlp::Decodable;
use async_trait::async_trait;
use base_consensus_derive::ChainProvider;
use base_proof_mpt::{OrderedListWalker, TrieNode, TrieProvider};
use base_proof_preimage::{CommsClient, PreimageKey, PreimageKeyType};
use base_protocol::BlockInfo;

use crate::{HintType, errors::OracleProviderError};

/// The oracle-backed L1 chain provider for the client program.
#[derive(Debug, Clone)]
pub struct OracleL1ChainProvider<T: CommsClient> {
    /// The L1 head hash.
    pub l1_head: B256,
    /// The preimage oracle client.
    pub oracle: Arc<T>,
}
```

**File:** crates/proof/proof/src/l2/chain_provider.rs (L1-20)
```rust
//! Contains the concrete implementation of the [`L2ChainProvider`] trait for the client program.

use alloc::{boxed::Box, sync::Arc, vec::Vec};

use alloy_consensus::{BlockBody, Header};
use alloy_eips::eip2718::Decodable2718;
use alloy_primitives::{Address, B256, Bytes};
use alloy_rlp::Decodable;
use async_trait::async_trait;
use base_common_consensus::{BaseBlock, BaseTxEnvelope};
use base_common_genesis::{RollupConfig, SystemConfig};
use base_consensus_derive::L2ChainProvider;
use base_proof_driver::PipelineCursor;
use base_proof_executor::TrieDBProvider;
use base_proof_mpt::{OrderedListWalker, TrieHinter, TrieNode, TrieProvider};
use base_proof_preimage::{CommsClient, PreimageKey, PreimageKeyType};
use base_protocol::{BatchValidationProvider, L2BlockInfo, to_system_config};
use spin::RwLock;

use crate::{HintType, eip2935::eip_2935_history_lookup, errors::OracleProviderError};
```
