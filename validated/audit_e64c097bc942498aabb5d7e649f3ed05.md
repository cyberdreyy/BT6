### Title
Unbounded Recursion in Fault-Proof `TrieNode::decode` / `OrderedListWalker::fetch_leaves` Allows Attacker-Controlled Preimages to Crash the Fault-Proof Program via Stack Overflow - ([File: crates/proof/mpt/src/node.rs])

### Summary
The Scriban advisory describes a `StackOverflowException` where deeply nested array-initializer syntax recurses through a parser path (`ParseArrayInitializer` → `ParseExpression` → `ParseArrayInitializer`) that bypasses an existing depth-limit safeguard. The analogous pattern exists in Base's fault-proof MPT decoder: `TrieNode::decode` recurses into itself for `Extension` nodes with no depth counter or limit, and `OrderedListWalker::fetch_leaves` recurses on `Branch`/`Extension` nodes while fetching preimages, also with no depth bound.

### Finding Description
`TrieNode::decode` in [1](#0-0)  peeks the RLP header and, for a 2-element list (leaf/extension), calls `Self::try_decode_leaf_or_extension_payload`, which for extension nodes recursively invokes `Self::decode(buf)` again on the nested payload: [2](#0-1) . There is no depth counter analogous to `ExpressionDepthLimit` guarding this recursive chain — each nested extension-node RLP encoding recurses the Rust call stack once more, exactly mirroring the Scriban `ParseArrayInitializer` recursion that bypassed the parser's expression-depth limit.

This decoder is reachable from attacker-influenced data: `OracleL1ChainProvider::trie_node_by_hash` calls `TrieNode::decode` directly on preimage bytes fetched from the (potentially adversarial) preimage oracle during L1 header/receipts/transactions trie derivation in the fault-proof program: [3](#0-2) . Critically, the hash-addressed preimage oracle model does **not** validate the RLP structure/depth before decoding — it only guarantees the returned bytes hash to the requested key, but a byte string that hashes correctly can still be maliciously structured (e.g., a long chain of one-child extension nodes, or via the `blind()`/`unblind()` recursive round-trip) to build/represent deep nesting, similar to how Scriban's parser accepted syntactically valid but arbitrarily deep nested arrays.

Separately, `OrderedListWalker::fetch_leaves` in [4](#0-3)  walks a `Branch`/`Extension` chain recursively, fetching a fresh preimage from the oracle at each blinded node and recursing without any depth limit — used to derive the receipts and transactions tries for every L1/L2 block consumed by the fault-proof program (`crates/proof/proof/src/l1/chain_provider.rs:75-88`, `crates/proof/proof/src/l1/chain_provider.rs:107-119`).

### Impact Explanation
The fault-proof program (op-program-style client used in Base's dispute game) runs inside a constrained execution environment (e.g., a MIPS/RISC-V zkVM or native fault-proof binary) with a bounded stack. A `StackOverflowException`/segfault in Rust cannot be caught with `catch_unwind` (analogous to .NET's uncatchable `StackOverflowException`) — the process/execution trace aborts. If a dispute-game participant (challenger or defender) can influence which preimages the honest program is forced to decode (e.g., by controlling L1 chain data referenced by the disputed output root, or by supplying maliciously crafted preimage data that still satisfies keccak256 pre-image checks structurally at each level), a stack overflow during trie derivation would cause the fault-proof program to crash mid-execution instead of producing a deterministic output root. This directly maps to "wrong provable output root" / "node halt" impact categories: an attacker could force any honest verifier attempting to reproduce the disputed claim to crash, preventing them from generating the correct proof and potentially allowing an invalid claim to go unchallenged or causing a dispute-game DoS.

### Likelihood Explanation
Likelihood is moderate: constructing a deeply nested extension-node chain requires crafting `depth` many single-child extension-node RLP encodings that all satisfy the correct chained keccak256 hash-preimage relationships terminating at the header's `receiptsRoot`/`transactionsRoot`. This is nontrivial to fit into a real Ethereum L1 block (block/receipt data size limits may cap achievable depth), but no explicit code-level depth cap exists to bound it — the only defenses are implicit (buffer size limits from L1 block gas/size limits), which is exactly the kind of "implicit, not explicit" protection that GHSA-p6q4-fgr8-vx4p demonstrates is insufficient once a dedicated recursive path is found.

### Recommendation
Add an explicit recursion-depth limit (mirroring `MAX_CBOR_NESTING_DEPTH` already used in [5](#0-4) ) to `TrieNode::decode`'s extension-node recursion in [2](#0-1)  and to `OrderedListWalker::fetch_leaves`'s recursive branch/extension traversal in [4](#0-3) , returning a decode error once the limit (e.g., 64, matching real MPT key-length bounds) is exceeded, rather than relying on implicit data-size limits.

### Proof of Concept
Conceptual PoC (cannot be fully executed without access to a live fault-proof harness, but the code path is directly traceable):
1. Construct a chain of N nested RLP-encoded `Extension` nodes, each wrapping the next as its "node" field, terminating in a `Leaf` node, such that each level's encoded bytes are ≤32 bytes (avoiding blinding) or correctly blinded/hashed to match parent references.
2. Set the outer trie root to reference this deep extension chain and place it as the `receiptsRoot` (or `transactionsRoot`) of an L1 block header known to the fault-proof program.
3. Have `OracleL1ChainProvider::receipts_by_hash` (`crates/proof/proof/src/l1/chain_provider.rs:68-89`) invoke `OrderedListWalker::try_new_hydrated`, which calls `TrieNode::decode` and `fetch_leaves` recursively per extension level.
4. For sufficiently large N (bounded only by how many extension-node levels can be encoded within the accessible preimage/RLP payload), the recursive call chain in `TrieNode::decode`/`fetch_leaves` overflows the stack, crashing the fault-proof program before it can compute the output root — exactly analogous to the Scriban PoC's `5000`-deep `[[[...]]]` nesting overflowing the parser's stack despite `ExpressionDepthLimit`.

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

**File:** crates/proof/proof/src/l1/chain_provider.rs (L125-143)
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

**File:** crates/proof/tee/registrar/src/cbor.rs (L13-13)
```rust
const MAX_CBOR_NESTING_DEPTH: usize = 64;
```
