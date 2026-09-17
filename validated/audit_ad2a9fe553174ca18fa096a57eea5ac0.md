### Title
Unbounded recursive `TrieNode` decoding/traversal in `base-proof-mpt` can stack-overflow the fault-proof program on adversarial preimages - ([File: crates/proof/mpt/src/node.rs])

### Summary
The MongoDB advisory describes a class of bug where recursive evaluation of attacker-controlled, deeply nested input has no periodic depth check, letting the recursion exhaust the stack. `base-proof-mpt`'s `TrieNode` implements exactly this pattern: `Decodable for TrieNode` recurses into child nodes (`Self::decode(buf)` for `Extension` payloads, `Vec::<Self>::decode` for `Branch` children) with no depth counter or limit anywhere in the crate. [1](#0-0) 

### Finding Description
`TrieNode::decode` recursively calls itself to decode nested `Extension`/`Branch` children directly from the untrusted RLP byte buffer, with no depth parameter or bound at all: [2](#0-1) [3](#0-2) 

A search of the whole `crates/proof/mpt` crate for any depth-limiting constant or parameter (`MAX_*_DEPTH`, `max_depth`, `depth:`) returns nothing, unlike other recursive/nested-structure decoders in this same repository that do bound recursion, e.g. the Nitro attestation CBOR parser (`MAX_CBOR_NESTING_DEPTH` threaded through every `read_at` call) and the observability-events JSON validator (`MAX_DATA_VALIDATION_DEPTH` in `find_forbidden_data_value`/`find_forbidden_data_key`): [4](#0-3) [5](#0-4) 

The other recursive `TrieNode` operations (`insert`, `collapse_if_possible`, and the `unblind`-then-recurse pattern) share the same unbounded-recursion structure: [6](#0-5) [7](#0-6) 

Per the crate's own README, `base-proof-mpt` is "a recursive, in-memory implementation of Ethereum's hexary Merkle Patricia Trie," used as the trie backend for `base-proof-executor`, which lazily fetches node preimages via a `TrieProvider` during stateless block execution/fault-proof verification: [8](#0-7) 

For a genuine Ethereum state/storage trie the nesting is implicitly bounded (≤64 nibbles for a 32-byte key), but the preimages fed to `TrieProvider`/`TrieNode::decode` in a fault-dispute context are supplied by an untrusted, potentially adversarial party (the opposing dispute participant or a malicious preimage source) reconstructing trie nodes from raw bytes — nothing in `TrieNode::decode` itself enforces that the decoded structure corresponds to a valid, bounded-depth Ethereum trie before recursing. An attacker can hand the fault-proof program a byte blob encoding a long chain of single-child `Extension`/`Branch` wrapper nodes (each only a few bytes), forcing `TrieNode::decode` (and subsequently `open`/`get`/`insert`/`unblind`/`collapse_if_possible`) to recurse to a depth proportional to input size rather than to trie key length.

### Impact Explanation
If the recursion depth exceeds the available stack, the fault-proof program crashes (Rust recursive stack overflow is a hard process abort, not a catchable panic). In the context of the dispute-game/fault-proof program this can prevent a party from producing a valid proof/output for a contested claim (node halt / inability to produce a provable output root for that step), which is one of the explicitly in-scope impacts (wrong provable output root / node halt) for a dispute-game participant supplying preimage data.

### Likelihood Explanation
Medium: reaching this code path requires the fault-proof/dispute-game execution path to decode an adversarially crafted trie-node byte sequence via `TrieProvider`, which is plausible for a dispute-game participant who controls preimage data for a contested trie root, but requires the input to actually be routed into `TrieNode::decode`/`open`/`insert` rather than being rejected earlier by RLP/structural well-formedness checks. No explicit external gate prevents an arbitrarily long chain of minimal Extension/Branch wrappers from being submitted as a preimage.

### Recommendation
Add an explicit depth parameter (mirroring the `depth`/`MAX_CBOR_NESTING_DEPTH` pattern already used in `crates/proof/tee/registrar/src/cbor.rs`) to `TrieNode::decode`, `open`, `get`, `insert`, `delete`, `unblind`, and `collapse_if_possible`, and reject any structure whose current recursion depth exceeds the maximum theoretically valid trie depth (64, for 32-byte keys) before recursing further.

### Proof of Concept
Conceptually: construct a byte sequence of many nested minimal RLP-encoded `Extension` nodes (`prefix` of length 0/1 nibble, wrapping another `Extension`), each adding only a few bytes, and feed it as a preimage to `TrieNode::decode`/`TrieProvider`-driven traversal (`open`/`get`) in the fault-proof executor. Because no depth counter exists, recursion depth scales with input size (bytes/~5) rather than being capped at 64, and a sufficiently large but still reasonably-sized input (tens of thousands of nested wrappers) can exhaust the thread stack, crashing the process running the fault-proof program before it can settle the dispute step.

### Citations

**File:** crates/proof/mpt/src/node.rs (L293-304)
```rust
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
```

**File:** crates/proof/mpt/src/node.rs (L363-424)
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

**File:** crates/common/observability-events/src/event.rs (L411-442)
```rust
fn find_forbidden_data_key(data: &Map<String, Value>, depth: usize) -> Option<ForbiddenDataReason> {
    if depth > MAX_DATA_VALIDATION_DEPTH {
        return Some(ForbiddenDataReason::TooDeep);
    }
    for (key, value) in data {
        if is_forbidden_data_key(key) {
            return Some(ForbiddenDataReason::Key(key.clone()));
        }
        if let Some(reason) = find_forbidden_data_value(value, depth + 1) {
            return Some(reason);
        }
    }
    None
}

fn find_forbidden_data_value(value: &Value, depth: usize) -> Option<ForbiddenDataReason> {
    if depth > MAX_DATA_VALIDATION_DEPTH {
        return Some(ForbiddenDataReason::TooDeep);
    }
    match value {
        Value::Object(child) => find_forbidden_data_key(child, depth),
        Value::Array(items) => {
            for item in items {
                if let Some(reason) = find_forbidden_data_value(item, depth + 1) {
                    return Some(reason);
                }
            }
            None
        }
        _ => None,
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
