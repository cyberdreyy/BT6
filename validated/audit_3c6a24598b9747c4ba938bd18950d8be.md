### Title
Unbounded recursive `TrieNode::decode` allows stack-overflow DoS of the fault-proof program via a single oracle-supplied preimage - ([File: crates/proof/mpt/src/node.rs])

### Summary
`TrieNode::decode` (used by the fault-proof program's `TrieProvider::trie_node_by_hash`) recursively decodes nested `Extension` nodes with no depth bound, mirroring the CVE-2022-25903 class of bug in `opcua` where unbounded nesting of `ExtensionObjects`/`Variants` caused a stack overflow. A dispute-game participant who controls the preimage data served through the fault-proof oracle can supply a single crafted RLP blob whose "trie node" is actually a deeply nested chain of `Extension` nodes, driving unbounded recursive calls into `TrieNode::decode` → `try_decode_leaf_or_extension_payload` → `TrieNode::decode` and crashing (stack-overflowing) the fault-proof program.

### Finding Description
`TrieNode::decode` peeks the RLP header and, for a 2-element list, calls `try_decode_leaf_or_extension_payload`, which for an extension-prefixed path recurses via `Self::decode(buf)` to decode the pointed-to sub-node: [1](#0-0) 

This recursive decode has no depth counter or nesting limit anywhere in the module or in `crates/proof/mpt/src/util.rs`'s `rlp_list_element_length` helper: [2](#0-1) 

The full `Decodable` implementation for `TrieNode` shows the dispatch: list decode either builds a `Branch` (`Vec::<Self>::decode`, itself recursive over up to 16 children) or falls into the leaf/extension path that recurses through `Self::decode` again: [3](#0-2) 

This is reachable in the fault-proof program: `OracleL1ChainProvider::trie_node_by_hash` feeds oracle-served preimage bytes directly into `TrieNode::decode` with no size/depth validation before recursive parsing begins: [4](#0-3) 

Because `TrieNode::decode` recurses purely on the *structure* of the bytes handed to it (an `Extension` node whose "node" field is itself another inline `Extension`-shaped RLP blob), an attacker does not need many separate preimages/hash hops — a single preimage blob can encode arbitrarily many nesting levels using only a few bytes per level, exactly analogous to the `opcua` `ExtensionObjects`/`Variants` nesting bug (CVE-2022-25903) that caused unbounded-depth stack overflow with a message smaller than the max size.

This differs from the CBOR attestation parser in `crates/proof/tee/registrar/src/cbor.rs`, which explicitly bounds recursion with `MAX_CBOR_NESTING_DEPTH`: [5](#0-4) 
No equivalent guard exists in the MPT `TrieNode` decoder.

### Impact Explanation
The fault-proof program (`crates/proof/proof`, `crates/proof/mpt`, `crates/proof/executor`) is the component that computes/verifies the provable output root for dispute games. A stack overflow there aborts the process running the trie-preimage decode, halting fault-proof execution for that game step. Depending on how the FPVM/host wraps this panic/crash, this can prevent an honest party from completing the dispute-game program run, i.e. denial of the fault-proof computation for the affected step, and can be triggered by any dispute-game participant supplying preimage data for a claimed trie node.

### Likelihood Explanation
Likelihood is high for a participant who controls preimage responses during fault-proof program execution: constructing a nested-`Extension` RLP blob requires no cryptographic work, only a few bytes per nesting level, and `TrieNode::decode` performs no depth or size check before recursing.

### Recommendation
Add an explicit maximum-recursion-depth (or convert to an iterative/stack-based decode) in `TrieNode::decode` / `try_decode_leaf_or_extension_payload` in `crates/proof/mpt/src/node.rs`, similar to the `MAX_CBOR_NESTING_DEPTH` guard already used in `crates/proof/tee/registrar/src/cbor.rs`, and reject RLP inputs that exceed the maximum plausible trie depth (bounded by key length in nibbles).

### Proof of Concept
Construct a single RLP buffer `B` as follows and feed it as the preimage returned by the oracle for `trie_node_by_hash`:
1. Innermost node: a valid RLP 2-element list `[path_nibble_byte, value]` (a `Leaf`).
2. Wrap it: `[path_nibble_byte, B]` repeated N times, each time treating the previous buffer as the "node" field of an `Extension`-typed 2-element list (`PREFIX_EXTENSION_EVEN`/`ODD` first nibble).
3. Choose N large enough (tens of thousands) so that `TrieNode::decode`'s recursive descent through `try_decode_leaf_or_extension_payload` exhausts the stack before returning — total buffer size can remain a few hundred KB while N drives recursion depth linearly, unlike normal tries where depth is bounded by key length (≤64 nibbles) because each level would normally require a separate keccak-preimage hop.
4. Serve this buffer as the response to `oracle.get(PreimageKey::new(*key, PreimageKeyType::Keccak256))` in `OracleL1ChainProvider::trie_node_by_hash`, causing `TrieNode::decode` to crash the fault-proof program with a stack overflow.

### Citations

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

**File:** crates/proof/mpt/src/util.rs (L63-77)
```rust
pub(crate) fn rlp_list_element_length(buf: &mut &[u8]) -> alloy_rlp::Result<usize> {
    let header = Header::decode(buf)?;
    if !header.list {
        return Err(alloy_rlp::Error::UnexpectedString);
    }
    let len_after_consume = buf.len() - header.payload_length;

    let mut list_element_length = 0;
    while buf.len() > len_after_consume {
        let header = Header::decode(buf)?;
        buf.advance(header.payload_length);
        list_element_length += 1;
    }
    Ok(list_element_length)
}
```

**File:** crates/proof/proof/src/l1/chain_provider.rs (L128-142)
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
    }
```

**File:** crates/proof/tee/registrar/src/cbor.rs (L13-13)
```rust
const MAX_CBOR_NESTING_DEPTH: usize = 64;
```
