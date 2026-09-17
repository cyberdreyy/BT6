### Title
Unbounded recursion in MPT `TrieNode::decode` allows stack-overflow DoS of the fault-proof program via a crafted preimage - (File: `crates/proof/mpt/src/node.rs`)

### Summary
`TrieNode::decode` recursively parses RLP-encoded Merkle-Patricia-Trie nodes with no recursion-depth limit and no enforcement of the 32-byte "blinding" rule that normally bounds how much unblinded sub-structure can be embedded in a single node. A single crafted preimage byte-string can encode an arbitrarily deep chain of nested `Extension`/`Branch` lists that will be decoded by unbounded recursive calls, exhausting the call stack of the fault-proof program (FPP) that consumes it. This mirrors the reported Django/GEOS bug class: recursive parsing of an attacker-suppliable, length-prefixed nested container type (there, `GEOMETRYCOLLECTION` WKT/WKB; here, RLP `TrieNode` lists) with no depth cap, causing unbounded recursion and a crash.

### Finding Description
`TrieNode::decode` peeks the RLP list length and dispatches: [1](#0-0) 

For a 2-element list it calls `try_decode_leaf_or_extension_payload`, which — for the `Extension` case — recursively invokes `Self::decode(buf)` on whatever RLP item follows in the buffer, with no depth counter and no check that the embedded child stays under the 32-byte blinding threshold that a compliant encoder would enforce: [2](#0-1) 

For a 17-element list (`Branch`), it recurses via `Vec::<Self>::decode(buf)`, which again calls `TrieNode::decode` for every child with no depth tracking: [3](#0-2) 

Nowhere in this decode path is a `depth` parameter threaded through, unlike the CBOR parser elsewhere in the same repo, which explicitly defends against exactly this bug class with a `MAX_CBOR_NESTING_DEPTH` counter passed through every recursive call: [4](#0-3) 

This `TrieNode::decode` is the direct entry point used by the fault-proof program's `TrieProvider` implementations to turn oracle-supplied preimage bytes into trie nodes, both for L1 and L2 chain data consumed during dispute-game execution: [5](#0-4) [6](#0-5) 

The preimage-oracle protocol only guarantees `keccak256(data) == key`; it does not guarantee that `data` was produced by a rule-compliant encoder that blinds (hashes) any child larger than 32 bytes, so nothing prevents a dispute-game participant from constructing a preimage whose "value" is a nested `Extension`/`Branch` RLP list rather than a genuine 32-byte hash reference.

### Impact Explanation
A dispute-game participant (or a party supplying a state/account/storage proof consumed by the FPP) can construct a single preimage that, once fetched, drives `TrieNode::decode` into unbounded recursion, crashing (stack overflow) the fault-proof program process during execution of a disputed step. Because dispute resolution depends on the FPP being able to deterministically execute and produce a provable output root, a crash at this stage prevents the game from resolving correctly — a node-halt / inability to compute the correct provable output for that step, which is one of the accepted high-impact categories (node halt / wrong provable output root) for fault-proof-program bugs.

### Likelihood Explanation
Constructing the malicious node is straightforward once the attacker controls the byte content of a preimage that will be hash-checked simply as `keccak256(data) == key` (the mechanism used by the on-chain/oracle preimage protocol for locally-supplied preimages during a dispute). No collision or preimage attack against keccak256 is required — the attacker simply picks arbitrary nested `Extension`/`Branch` RLP bytes as the data itself and lets its hash be the key that is claimed for that data. The only constraint is reaching a step in the disputed trace where such a fabricated node is fetched via `trie_node_by_hash`, which is a normal part of L1/L2 state traversal reachable from any dispute-game participant.

### Recommendation
Add an explicit recursion-depth counter to `TrieNode::decode` (and its private helpers `try_decode_leaf_or_extension_payload`, and the `Vec::<Self>::decode` path for branches), capping nesting to a small constant (e.g., mirroring `MAX_CBOR_NESTING_DEPTH` in `crates/proof/tee/registrar/src/cbor.rs`), and reject decode attempts that exceed it. Additionally, enforce that any raw/unblinded embedded child's total RLP length is ≤ 32 bytes (the actual MPT blinding invariant), rejecting any decode where an "opened" (non-`Blinded`) child would violate this, which independently bounds recursion depth to the real protocol's key-length limit (≤64 nibbles).

### Proof of Concept
Conceptually: build a byte string `B` consisting of `N` (e.g. 100,000) nested single-nibble RLP `Extension` list headers (`0xc2 <path_byte>` wrapping the next level) terminated by a `Leaf`, entirely embedded (not `Blinded`) — i.e. never inserting a 32-byte hash reference — so the whole thing is one contiguous buffer with no separate preimage fetches required for intermediate levels. Register `B` as the preimage for `key = keccak256(B)` with the fault-proof program's preimage oracle (as is done in the tests below), then call:
```rust
TrieNode::decode(&mut B.as_slice())
```
Each level of the crafted structure causes another recursive call to `Self::decode` (via `try_decode_leaf_or_extension_payload`), with no depth check anywhere in the call path shown above, exhausting the stack. The existing test harness demonstrates the same recursive decode entry point used by real trie fetchers: [7](#0-6)

### Citations

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

**File:** crates/proof/mpt/src/node.rs (L567-590)
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

**File:** crates/proof/proof/src/l1/chain_provider.rs (L128-141)
```rust
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
```

**File:** crates/proof/proof/src/l2/chain_provider.rs (L176-190)
```rust
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

**File:** crates/proof/mpt/src/test_util.rs (L143-153)
```rust
    fn trie_node_by_hash(&self, key: B256) -> Result<TrieNode, TestTrieProviderError> {
        TrieNode::decode(
            &mut self
                .preimages
                .get(&key)
                .cloned()
                .ok_or(TestTrieProviderError("key not found in trie"))?
                .as_ref(),
        )
        .map_err(|_| TestTrieProviderError("failed to decode trie node"))
    }
```
