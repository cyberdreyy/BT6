### Title
Unbounded recursive RLP decoding of `TrieNode` (no depth limit) enables stack-overflow DoS in the fault-proof MPT — ([File: crates/proof/mpt/src/node.rs])

### Summary
`TrieNode::decode` in `crates/proof/mpt/src/node.rs` recursively decodes nested MPT structures (`Extension`/`Branch` nodes) with no maximum nesting-depth check, unlike other untrusted-input parsers in the same repository (e.g. the CBOR attestation parser, which explicitly enforces `MAX_CBOR_NESTING_DEPTH`). A crafted preimage blob with deeply nested `Extension` nodes can drive unbounded Rust call-stack recursion, causing a stack overflow — the same bug class as CVE-2018-7453 (`AcroForm::scanField` unbounded recursion parsing a crafted PDF), just applied to RLP/MPT node parsing instead of PDF AcroForm parsing.

### Finding Description
`TrieNode::decode` [1](#0-0)  dispatches on RLP list length: a 2-element list is routed to `try_decode_leaf_or_extension_payload`, which for `Extension` nodes recursively calls `Self::decode(buf)` on the nested pointer [2](#0-1) . Because each `Extension → Extension → …` hop only needs a short RLP list header plus a 1+-nibble path before pointing at the next nested node, an attacker can construct a compact blob with thousands of nesting levels, each adding one native stack frame to a single `TrieNode::decode` call chain.

Unlike the analogous nested-structure parser in this codebase — `CborItem::read_at` in `crates/proof/tee/registrar/src/cbor.rs`, which explicitly bounds recursion with `MAX_CBOR_NESTING_DEPTH` [3](#0-2)  — `TrieNode::decode`/`try_decode_leaf_or_extension_payload` and the traversal helpers that recurse across hash-linked preimages (`open`, `delete`, `collapse_if_possible`, `OrderedListWalker::fetch_leaves`) impose no such bound [4](#0-3) [5](#0-4) .

This module is explicitly the "fault-proof program and MPT" backend used for stateless block execution/derivation [6](#0-5) , and is driven by attacker-suppliable preimages fetched via `TrieProvider`/`TrieDBProvider` during dispute-game execution (e.g. `TrieDB::get_trie_account`/`storage`) [7](#0-6) [8](#0-7) . A dispute-game participant supplying preimages for the disputed state can therefore feed a maliciously nested `Extension` chain into `TrieNode::decode`/`open`, without any depth cap catching it before the recursion exhausts the stack.

### Impact Explanation
A successful attack overflows the native call stack of the fault-proof program while it decodes/traverses trie nodes to resolve an account or storage slot during dispute resolution. This crashes the process performing the derivation/verification (analogous to the sigsegv-on-stack-overflow behavior this repo's own CLI test harness explicitly checks for elsewhere [9](#0-8) ). A crash of the fault-proof program during a dispute prevents the honest party from computing/verifying the correct output root for that step, which can stall or corrupt dispute-game resolution — a node-halt / wrong-provable-output-root class impact in the fault-proof path explicitly in scope.

### Likelihood Explanation
Likelihood depends on whether the untrusted preimage supplied by a dispute-game participant can freely encode an arbitrarily deep `Extension` chain while still hashing to a hash value the honest party's execution will actually dereference; this requires further verification against the exact preimage-oracle/hash-binding semantics used in the dispute game (not fully visible in the indexed code), which I could not fully confirm from the available snippets. The recursion itself is unambiguously unbounded in the decode/traversal code shown, and the pattern is reachable purely from data supplied by an untrusted participant in the fault-proof/dispute flow, one of the explicitly in-scope attacker classes.

### Recommendation
Add an explicit maximum nesting-depth parameter (mirroring `MAX_CBOR_NESTING_DEPTH` in `crates/proof/tee/registrar/src/cbor.rs`) to `TrieNode::decode`, `try_decode_leaf_or_extension_payload`, `open`, `delete`, `collapse_if_possible`, and `OrderedListWalker::fetch_leaves`, rejecting any structure whose recursion depth exceeds the maximum theoretically possible for a 256-bit key path (64 nibbles) plus a small safety margin, and convert the traversal functions to an explicit iterative worklist where feasible to remove stack-depth dependence on attacker-controlled input entirely.

### Proof of Concept
Not independently constructed/verified in this pass — I identified the missing depth bound by direct code inspection of the recursive decode/traversal functions and contrasted it with the depth-bounded CBOR parser in the same repository, but did not have tool access to build and run a concrete overflow-triggering preimage against the fault-proof program to confirm exploitability end-to-end. Confirming this would require constructing a nested `Extension`-chain RLP blob and driving it through the actual `TrieDBProvider`/preimage-oracle path used in a live dispute game.

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

**File:** crates/proof/mpt/src/node.rs (L452-461)
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

**File:** crates/proof/mpt/src/list_walker.rs (L86-129)
```rust
    /// Traverses a [`TrieNode`], returning all values of child [`TrieNode::Leaf`] variants.
    fn fetch_leaves(
        trie_node: &TrieNode,
        fetcher: &F,
    ) -> OrderedListWalkerResult<VecDeque<(Bytes, Bytes)>> {
        match trie_node {
            TrieNode::Branch { stack } => {
                let mut leaf_values = VecDeque::with_capacity(stack.len());
                for item in stack {
                    match item {
                        TrieNode::Blinded { commitment } => {
                            // If the string is a hash, we need to grab the preimage for it and
                            // continue recursing.
                            let trie_node = Self::get_trie_node(commitment.as_ref(), fetcher)?;
                            leaf_values.append(&mut Self::fetch_leaves(&trie_node, fetcher)?);
                        }
                        TrieNode::Empty => { /* Skip over empty nodes, we're looking for values. */
                        }
                        item => {
                            // If the item is already retrieved, recurse on it.
                            leaf_values.append(&mut Self::fetch_leaves(item, fetcher)?);
                        }
                    }
                }
                Ok(leaf_values)
            }
            TrieNode::Leaf { prefix, value } => {
                Ok(vec![(prefix.to_vec().into(), value.clone())].into())
            }
            TrieNode::Extension { node, .. } => {
                // If the node is a hash, we need to grab the preimage for it and continue
                // recursing. If it is already retrieved, recurse on it.
                match node.as_ref() {
                    TrieNode::Blinded { commitment } => {
                        let trie_node = Self::get_trie_node(commitment.as_ref(), fetcher)?;
                        Ok(Self::fetch_leaves(&trie_node, fetcher)?)
                    }
                    node => Ok(Self::fetch_leaves(node, fetcher)?),
                }
            }
            TrieNode::Empty => Ok(VecDeque::new()),
            _ => Err(TrieNodeError::InvalidNodeType.into()),
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

**File:** crates/proof/executor/src/db/mod.rs (L132-153)
```rust
    pub fn get_trie_account(
        &mut self,
        address: &Address,
        block_number: u64,
    ) -> TrieDBResult<Option<TrieAccount>> {
        // Send a hint to the host to fetch the account proof.
        self.hinter
            .hint_account_proof(*address, block_number)
            .map_err(|e| TrieDBError::Provider(e.to_string()))?;

        // Fetch the account from the trie.
        let hashed_address_nibbles = Nibbles::unpack(keccak256(address.as_slice()));
        let Some(trie_account_rlp) = self.root_node.open(&hashed_address_nibbles, &self.fetcher)?
        else {
            return Ok(None);
        };

        // Decode the trie account from the RLP bytes.
        TrieAccount::decode(&mut trie_account_rlp.as_ref())
            .map_err(TrieNodeError::RLPError)
            .map_err(Into::into)
            .map(Some)
```

**File:** crates/proof/executor/src/db/mod.rs (L304-335)
```rust
    fn storage(&mut self, address: Address, index: U256) -> Result<U256, Self::Error> {
        // Send a hint to the host to fetch the storage proof.
        self.hinter
            .hint_storage_proof(address, index, self.parent_block_header.number)
            .map_err(|e| TrieDBError::Provider(e.to_string()))?;

        // Fetch the account's storage root from the cache. If storage is being accessed, the
        // account should have been loaded into the cache by the `basic` method. If the account was
        // non-existing, the storage root will not be present.
        match self.storage_roots.get_mut(&address) {
            None => {
                // If the storage root for the account does not exist, return zero.
                Ok(U256::ZERO)
            }
            Some(storage_root) => {
                // Fetch the storage slot from the trie.
                let hashed_slot_key = keccak256(index.to_be_bytes::<32>().as_slice());
                match storage_root.open(&Nibbles::unpack(hashed_slot_key), &self.fetcher)? {
                    Some(slot_value) => {
                        // Decode the storage slot value.
                        let int_slot = U256::decode(&mut slot_value.as_ref())
                            .map_err(TrieNodeError::RLPError)?;
                        Ok(int_slot)
                    }
                    None => {
                        // If the storage slot does not exist, return zero.
                        Ok(U256::ZERO)
                    }
                }
            }
        }
    }
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
