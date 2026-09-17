### Title
Unchecked subtraction on attacker-influenced RLP `payload_length` can underflow in the fault-proof MPT trie-node decoder - (File: `crates/proof/mpt/src/util.rs`)

### Summary
`rlp_list_element_length`, used by `TrieNode::decode` to determine whether an RLP list node is a 17-element branch or a 2-element leaf/extension, computes `buf.len() - header.payload_length` without first checking that `header.payload_length <= buf.len()`. Every other RLP-list decoder in this codebase (`TxDeposit::rlp_decode`, `DepositReceipt::rlp_decode_with_bloom`, `TxEip8130::decode`, `SpanBatchEip8130TransactionData::decode`, etc.) explicitly guards this exact computation with `if header.payload_length > remaining { return Err(InputTooShort) }` before doing arithmetic on the declared length. `rlp_list_element_length` omits that guard.

### Finding Description [1](#0-0) 

```
pub(crate) fn rlp_list_element_length(buf: &mut &[u8]) -> alloy_rlp::Result<usize> {
    let header = Header::decode(buf)?;
    if !header.list {
        return Err(alloy_rlp::Error::UnexpectedString);
    }
    let len_after_consume = buf.len() - header.payload_length;   // <-- unchecked
    ...
```

`header.payload_length` is a raw, attacker/prover-controlled value decoded directly from the RLP header bytes; nothing in `Header::decode` (as used consistently elsewhere in this repo, where callers always re-check it against `buf.len()`) guarantees it is bounded by the number of bytes actually remaining in `buf`. If a crafted/malformed trie node declares a `payload_length` larger than the remaining slice, `buf.len() - header.payload_length` underflows `usize`.

This differs from every other "`started - buf.len()`" idiom used elsewhere in the codebase (e.g. `SignedChange::decode`, `TxEip8130::decode`, `SpanBatchEip8130TransactionData::decode`), which compute the subtraction the safe way round — `started_len - buf.len()` *after* consuming bytes, where `buf.len()` can only have shrunk, guaranteeing no underflow: [2](#0-1) 

The MPT function instead subtracts the *unvalidated declared length* from `buf.len()` before any consumption, which is the same bug class as CVE-2024-6381 (an integer computation on an untrusted length value producing an out-of-range/negative result without validation).

This code path (`TrieNode::decode` → `rlp_list_element_length`) is exercised in the fault-proof program when decoding MPT nodes fetched from the preimage oracle while walking account/storage proofs — an explicitly in-scope surface ("the fault-proof program and MPT"): [3](#0-2) 

### Impact Explanation
`usize` subtraction underflow in Rust:
- If the crate is compiled with `overflow-checks = true` (common for consensus/fault-proof crates to catch corruption deterministically), this triggers an unconditional arithmetic-overflow panic, aborting the fault-proof program mid-execution — a node/prover halt on a maliciously crafted (or merely malformed) trie node supplied through the dispute-game preimage path.
- If overflow checks are disabled, the value wraps to a huge `usize`, so the subsequent `while buf.len() > len_after_consume` loop never executes and `list_element_length` is reported as `0`. That miscount routes a genuinely list-typed node into the `_ => Err(UnexpectedLength)` arm in `TrieNode::decode`, turning what should be node processing into a decode error.

Either outcome (panic/halt, or a decode-path divergence) is a concrete, reachable defect in the fault-proof program's MPT handling, which the analog rules call out as in-scope for wrong/aborted output computation.

### Likelihood Explanation
`rlp_list_element_length` is reached for every list-typed RLP item decoded as a `TrieNode`, including nodes retrieved from the preimage oracle during dispute-game execution before any hash/commitment re-derivation has taken place on the raw bytes. A participant able to supply or influence raw preimage bytes for a claimed trie node hash can trigger the malformed-header condition; I could not fully confirm from the available index whether `Header::decode` performs any independent bound check against `buf.len()` at the alloy_rlp layer (this crate is a third-party dependency, not part of the indexed repo), nor could I confirm this crate's `overflow-checks` Cargo profile setting — both of which affect exact severity and exploit reliability. These are the two open uncertainties for this analog.

### Recommendation
Add the same explicit bounds check used everywhere else in this codebase before performing the subtraction:
```rust
let remaining = buf.len();
if header.payload_length > remaining {
    return Err(alloy_rlp::Error::InputTooShort);
}
let len_after_consume = remaining - header.payload_length;
```

### Proof of Concept
Construct a byte slice whose RLP header declares `list: true` with a `payload_length` larger than the number of bytes actually following the header (e.g., a single-byte list header claiming a 64-byte payload but supplying only 4 trailing bytes), and pass it to `TrieNode::decode` (which calls `rlp_list_element_length` at `crates/proof/mpt/src/node.rs:575`). With `overflow-checks` enabled this panics inside `rlp_list_element_length` at the `buf.len() - header.payload_length` line; a full runtime PoC would require building the crate with that profile to observe the panic directly, which I was not able to execute in this read-only analysis.

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

**File:** crates/common/consensus/src/transaction/eip8130/account_changes.rs (L273-279)
```rust
        let started_len = buf.len();
        let op = u8::decode(buf)?;
        let change_type = ChangeType::from_op_byte(op)
            .ok_or(alloy_rlp::Error::Custom("invalid SignedChange op byte"))?;
        let payload = Bytes::decode(buf)?;
        let consumed = started_len - buf.len();
        if consumed != header.payload_length {
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
