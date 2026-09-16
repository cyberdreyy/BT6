### Title
Unbounded recursion in `TrieNode::decode` allows stack-overflow DoS in the fault-proof program via attacker-supplied MPT preimages - (File: crates/proof/mpt/src/node.rs)

### Summary
`TrieNode::decode` (the Merkle-Patricia-Trie node decoder used by the fault-proof program) recursively decodes `Branch` and `Extension` nodes with no depth limit, analogous to the unbounded-recursion JSON-parsing stack overflow described in CVE-2025-6710/BIT-mongodb-2025-6710.

### Finding Description
`TrieNode::decode` at [1](#0-0)  dispatches on the RLP list length: for a `BRANCH_LIST_LENGTH` it calls `Vec::<Self>::decode(buf)`, and for a leaf/extension shape it calls `Self::try_decode_leaf_or_extension_payload`, which itself recursively calls `Self::decode(buf)` for `Extension` nodes at [2](#0-1) . Because `Branch { stack: Vec<Self> }` decoding via `Vec::<Self>::decode` and `Extension { node: Box<Self> }` decoding both recurse into `TrieNode::decode` again, an attacker who controls the RLP-encoded trie-node bytes fed to this decoder can construct a deeply nested chain of extension/branch nodes that drives the decode call stack arbitrarily deep, with no `MAX_DEPTH`/`depth` counter anywhere in this file (unlike the CBOR parser in `crates/proof/tee/registrar/src/cbor.rs`, which explicitly checks `depth > MAX_CBOR_NESTING_DEPTH` at [3](#0-2) ). This trie-node data is preimage data supplied by the untrusted L1/L2 data source consumed by the OP-stack fault-proof program (`crates/proof/proof`), i.e., attacker-influenced preimage/oracle content that a dispute-game participant can shape, matching the report's precondition of "specifically crafted... inputs [that] induce unwarranted levels of recursion."

### Impact Explanation
If reachable with attacker-chosen preimage bytes, this would crash the fault-proof program process via stack overflow before/at proof execution, potentially halting proof generation for a dispute game or producing a non-deterministic/incorrect outcome (host process abort) rather than a clean invalid-proof rejection — a node-halt / wrong-output class impact for the on-chain dispute path.

### Likelihood Explanation
I could not fully verify, within the available context, (a) whether this decode path is actually reached from data whose *content* is attacker-controlled end-to-end (as opposed to being validated against a known, honest-derived root hash before decoding, which would make deep-nesting infeasible without also matching a keccak256 preimage commitment), or (b) whether an outer caller (e.g., `OrderedListWalker` in `crates/proof/mpt/src/list_walker.rs`) already imposes a depth bound before invoking `TrieNode::decode`. Because MPT nodes are typically content-addressed by hash (`Blinded { commitment: B256 }`), constructing a very deep, validly-hashed chain of nested nodes requires the attacker to actually find/produce those preimages, which is possible only if the attacker supplies the entire nested proof chain (e.g., for their own account/storage slots) — this is plausible in principle for a dispute-game participant supplying preimages for a state they control, but I was not able to confirm the concrete reachability chain (preimage oracle validation order, and any depth caps already enforced by `crates/proof/proof` or `crates/proof/preimage`) within the remaining investigation budget.

### Recommendation
Add an explicit recursion-depth counter/limit to `TrieNode::decode` (mirroring `MAX_CBOR_NESTING_DEPTH` in `crates/proof/tee/registrar/src/cbor.rs`), returning a decode error once a bounded depth (e.g., matching the maximum possible MPT depth for the address/storage key space, ~64-128) is exceeded, rather than relying on unbounded native call-stack recursion.

### Proof of Concept
Not constructed — building a concrete PoC requires confirming exact preimage-oracle validation ordering (whether node bytes are decoded before or after their keccak256 commitment is checked against an expected parent hash) in `crates/proof/proof` and `crates/proof/mpt/src/list_walker.rs`, which I could not fully trace in the remaining tool budget.

### Citations

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
