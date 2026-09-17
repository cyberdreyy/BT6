### Title
Panic-inducing integer underflow in `rlp_list_element_length` allows a crafted MPT trie-node preimage to crash the fault-proof program - (File: `crates/proof/mpt/src/util.rs`)

### Summary
`TrieNode::decode` in `crates/proof/mpt/src/node.rs` peeks at an RLP header and, for list-typed nodes, calls `rlp_list_element_length` to determine whether the node is a branch (17 elements) or leaf/extension (2 elements) before dispatching to the appropriate decode path. `rlp_list_element_length` computes `buf.len() - header.payload_length` without first checking that `header.payload_length <= buf.len()`, unlike sibling decoders in this codebase (e.g. `TxDeposit::rlp_decode`, `AccountChange::decode`, `TxEip8130` decoders) which explicitly guard against `payload_length` exceeding the remaining buffer before subtracting.

### Finding Description
`Header::decode` (from `alloy_rlp`) only parses the length prefix bytes themselves; it does not, in the code paths used throughout this codebase, guarantee that the declared `payload_length` is actually available in the remaining buffer — this is why every other RLP consumer here (`TxDeposit::rlp_decode`, `BaseReceipt::rlp_decode_with_bloom`, `AccountChange::decode`, `SpanBatchEip8130TransactionData::decode`, `TxEip8130::rlp_decode_signed`) manually checks `header.payload_length > remaining` and returns `InputTooShort`/`ListLengthMismatch` before subtracting lengths.

`rlp_list_element_length` skips this guard: [1](#0-0) 

If an attacker (via a fault-proof preimage the challenger/oracle must decode as a trie node, or any other caller of `TrieNode::decode`) supplies a list header whose declared `payload_length` is larger than the bytes actually present in `buf`, `buf.len() - header.payload_length` underflows. In a build with overflow checks enabled this triggers an unconditional panic (arithmetic overflow), immediately aborting the process; in a build without overflow checks this silently wraps to a huge `usize`, which happens to make the subsequent loop a no-op (returning `Ok(0)`) rather than crash, but the underflow is still undefined-intent code reachable purely from attacker-supplied bytes with no other validation.

This path is invoked directly from `TrieNode::decode`: [2](#0-1) 

`TrieNode::decode` is the entry point used by every `TrieProvider::trie_node_by_hash` implementation that backs the fault-proof program's stateless trie access, e.g.: [3](#0-2) [4](#0-3) 

These providers decode preimages fetched from an untrusted preimage oracle inside the fault-proof program — data that is ultimately supplied by whichever party posts/serves the L1/L2 pre-images used to derive the state root during dispute-game execution. A malformed/adversarial trie-node preimage that declares an oversized list `payload_length` therefore reaches `rlp_list_element_length`'s unchecked subtraction.

### Impact Explanation
The CVE class here is exactly analogous to CVE-2021-46547 (Cesanta MJS SEGV parsing bug leading to DoS): a malformed but attacker-reachable input hits an unchecked arithmetic/bounds operation deep in a parser, aborting the process. Here the reachable consequence is a panic (process abort / node halt) in the fault-proof program if compiled with overflow checks (a fairly common practice for zkVM/on-chain-provable executables to guarantee deterministic behavior), which would prevent a legitimate party from producing the fault-proof program's output for a dispute-game move — a node-halt / cannot-serve-required-computation class of impact matching the "node halt" bucket. In builds without overflow checks the underflow does not crash but is a latent correctness bug that a hardened build would turn into a crash.

### Likelihood Explanation
The bug is only reachable when the RLP header claims a `list = true` payload length exceeding what remains in the actual buffer for a preimage handed to `TrieNode::decode`. Because every other decoder in this codebase treats this exact scenario (declared length > remaining bytes) as a routinely-crafted malicious input worth explicitly guarding against, it is a realistic condition for an adversarial preimage. However, exploitability depends on the build's overflow-check configuration, which could not be confirmed from the repository (no `overflow-checks` setting was found in workspace `Cargo.toml`s, meaning it likely defaults to Rust's standard profile behavior: enabled in `dev`/debug, disabled in `release`). This uncertainty tempers confidence that this is exploitable against a production `release` build, though it remains a genuine unchecked-arithmetic defect exactly matching the analog bug class.

### Recommendation
Add an explicit bounds check in `rlp_list_element_length` (and anywhere else `buf.len() - header.payload_length`-style subtraction occurs) before performing the subtraction, mirroring the pattern already used elsewhere in the codebase:
```rust
if header.payload_length > buf.len() {
    return Err(alloy_rlp::Error::InputTooShort);
}
let len_after_consume = buf.len() - header.payload_length;
```

### Proof of Concept
Construct a byte buffer whose RLP list header declares a `payload_length` larger than the number of bytes actually supplied, then feed it to `TrieNode::decode` (e.g. via a `TrieProvider::trie_node_by_hash` implementation backed by attacker-influenced preimage data):
```rust
// 0xf8 0xff -> "long list" header claiming 0xff (255) bytes of payload,
// but only a handful of trailing bytes are actually present.
let malformed: [u8; 4] = [0xf8, 0xff, 0x00, 0x00];
let _ = base_proof_mpt::TrieNode::decode(&mut malformed.as_slice());
```
In a build with `overflow-checks = true`, this call panics inside `rlp_list_element_length` due to the `buf.len() - header.payload_length` underflow (`buf.len() == 2` after the header is consumed, `header.payload_length == 255`), aborting the process before `TrieNode::decode` can return an error to the caller.

### Citations

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
