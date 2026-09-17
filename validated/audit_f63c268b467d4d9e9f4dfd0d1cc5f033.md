Confirmed: `TrieNode::decode` in `crates/proof/mpt/src/node.rs` is invoked directly on attacker/L1-influenced preimage bytes fetched via `OracleL1ChainProvider::trie_node_by_hash` and `OracleL2ChainProvider::trie_node_by_hash`, i.e., data supplied by a dispute-game participant / preimage oracle during fault-proof execution. [1](#0-0) [2](#0-1) 

### Title
Unchecked RLP payload-length subtraction in `rlp_list_element_length` causes integer underflow / panic on attacker-supplied trie-node preimages - (File: crates/proof/mpt/src/util.rs)

### Summary
`rlp_list_element_length`, used by `TrieNode::decode` to classify RLP-encoded trie nodes (branch vs. leaf/extension), computes `len_after_consume = buf.len() - header.payload_length` without checking that `header.payload_length <= buf.len()`.

### Finding Description
`Header::decode` only parses the declared length prefix of an RLP item; it does not itself guarantee that the buffer actually contains that many remaining bytes for the payload. `rlp_list_element_length` uses the declared `header.payload_length` directly in a `usize` subtraction: [3](#0-2) 
If a malicious/malformed trie-node preimage declares an outer list `payload_length` larger than the actual remaining buffer, `buf.len() - header.payload_length` underflows. In debug builds this panics immediately; in release builds (as typically used for the on-chain/zkVM fault-proof program) the wrapped `len_after_consume` becomes a huge value, causing the `while buf.len() > len_after_consume` loop condition to be evaluated against corrupted state, and the subsequent inner `Header::decode`/`buf.advance(header.payload_length)` calls to walk past the true end of the buffer using declared-but-unverified lengths taken directly from attacker data. This is directly analogous to the CVE-2021-34121 bug class: a parser trusts a length field from untrusted tree/list data without validating it against the actual buffer bounds before using it to advance/read, producing an out-of-bounds read (and, on this backend, either a panic/halt or corrupted parse state) driven purely by attacker-controlled bytes.

This function is reached from `TrieNode::decode`, which is the single entry point used to decode every trie-node preimage supplied through `TrieProvider::trie_node_by_hash` in the fault-proof program on both the L1 and L2 provider paths — i.e., every proof node a dispute-game participant/preimage-oracle serves during derivation and state verification passes through this exact code with no independent bounds validation: [4](#0-3) 

### Impact Explanation
The fault-proof program processes MPT/account/storage proof nodes that are ultimately backed by data an adversarial dispute-game participant controls via the preimage oracle. A crafted preimage with an inconsistent RLP length can drive `rlp_list_element_length` into an integer-underflow condition, corrupting the loop's termination bound and causing either (a) a panic that halts the fault-proof program (a node/program halt, potentially preventing valid dispute resolution), or (b) a malformed element count being returned to `TrieNode::decode`'s branch/leaf dispatch, which can misclassify the node and lead to further out-of-bounds reads while advancing through the buffer, risking a wrong or unprovable output root during dispute resolution.

### Likelihood Explanation
Any dispute-game participant who can supply proof preimages to the fault-proof program (via the preimage oracle / hinting flow) controls the raw bytes decoded by `TrieNode::decode`, so triggering the miscomputed length requires only crafting a single RLP header with a payload length exceeding the actual remaining bytes — no privileged access is needed.

### Recommendation
In `rlp_list_element_length`, validate `header.payload_length <= buf.len()` (and similarly validate each inner element's `header.payload_length` against remaining bytes) before performing the subtraction/advance, returning `alloy_rlp::Error::ListLengthMismatch`/`Overrun`-style errors instead of allowing the arithmetic to proceed on unchecked, attacker-declared lengths.

### Proof of Concept
Construct a byte buffer beginning with an RLP list header (`0xc0`-prefixed or long-form) that declares a `payload_length` larger than the number of bytes actually following it (e.g., a single-byte buffer `[0xf8, 0xff]` declaring a long list of 0xff bytes with no payload present), and feed it to `TrieNode::decode(&mut buf.as_ref())`. This reaches `rlp_list_element_length`, where `buf.len() - header.payload_length` underflows because `header.payload_length` (255) exceeds `buf.len()` (0 remaining after the header), yielding a panic in debug builds or a wrapped/garbage `len_after_consume` in release builds that corrupts the subsequent element-counting loop. [5](#0-4)

### Citations

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

**File:** crates/proof/proof/src/l2/chain_provider.rs (L176-189)
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
