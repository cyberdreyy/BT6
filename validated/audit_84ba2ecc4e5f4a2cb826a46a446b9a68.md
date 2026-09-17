### Title
Panic-inducing integer underflow/OOB advance in `rlp_list_element_length` when decoding untrusted MPT preimages - (File: `crates/proof/mpt/src/util.rs`)

### Summary
`rlp_list_element_length` in the fault-proof MPT crate walks an RLP list's sub-headers to count elements, but never validates that a claimed element `payload_length` actually fits inside the remaining buffer before subtracting/advancing, unlike every other RLP decoder in this codebase which explicitly checks `buf.len() < header.payload_length` first.

### Finding Description
`rlp_list_element_length` decodes an outer RLP header and then computes `len_after_consume = buf.len() - header.payload_length` and loops, calling `Header::decode` on each element and then `buf.advance(header.payload_length)`: [1](#0-0) 

`alloy_rlp::Header::decode` only parses the length-prefix bytes; it does not verify the declared `payload_length` is within the remaining buffer (which is why every other decoder in this codebase — e.g. `DepositReceipt::rlp_decode_with_bloom`, `TxDeposit::rlp_decode`, `TxEip8130::decode_calls` — explicitly checks `buf.len() < header.payload_length` before slicing/advancing): [2](#0-1) [3](#0-2) 

`rlp_list_element_length` is missing this check both for the outer header (`buf.len() - header.payload_length` can underflow `usize`, which panics on overflow-checked builds or silently corrupts the loop bound on release builds) and for each inner element header inside the `while` loop, where `buf.advance(header.payload_length)` will panic if the claimed length exceeds the remaining slice.

This function is invoked directly from `TrieNode::decode`, the primary decoder for Merkle-Patricia-Trie nodes used throughout `base-proof-mpt`: [4](#0-3) 

`TrieNode::decode` is reached whenever a preimage is fetched and parsed via `TrieProvider::trie_node_by_hash`, which is exactly how the fault-proof program resolves L1/L2 state, receipts, and transaction trie nodes supplied by the (untrusted/adversarial) preimage oracle: [5](#0-4) 

Because the preimage bytes are attacker-influenced content that must decode successfully for the fault-proof/dispute program to make progress, a single 2-item or 17-item RLP list header with a bogus, oversized `payload_length` for one of its declared elements will crash the decoder with a Rust panic rather than returning a decode error.

### Impact Explanation
This is directly analogous to the underlying CVE-2019-20631 bug class: a list/element-count routine that trusts an attacker-supplied length field without bounds-checking against the actual buffer, causing an invalid dereference/out-of-bounds access (here, a Rust panic from `usize` subtraction underflow or `Buf::advance` past the end of the slice) when parsing a crafted file/blob. In this codebase the "crafted file" is a crafted MPT node preimage fed to the fault-proof program. A panic inside the trie-node decoder halts the process that hit it — i.e., it can crash the fault-proof/dispute verification program mid-execution instead of yielding a clean RLP decode error, which is a denial-of-service on the party executing the fault proof (proposer/challenger) and, if reached deterministically inside the on-chain-equivalent zk/fault program, can prevent production of a valid output root for that step.

### Likelihood Explanation
Reaching this requires supplying a maliciously crafted trie-node preimage (raw bytes) whose outer RLP list declares payload_length that doesn't match the actual bytes, or whose sub-element header claims more bytes than remain. Any actor who can influence preimages consumed by the fault-proof program (a dispute-game participant providing preimage data, or a malicious/faulty L1/L2 data source feeding the chain providers) can trigger this deterministically with a small crafted byte string — no cryptographic work or gas cost is required, only correct RLP header framing bytes.

### Recommendation
Add the same explicit bounds check used elsewhere in the codebase before subtracting or advancing: verify `buf.len() >= header.payload_length` for both the outer header and every inner element header inside the loop in `rlp_list_element_length`, returning `alloy_rlp::Error::InputTooShort` (or equivalent) instead of allowing the subtraction/`advance` to panic. Also add a fuzz/unit test with a truncated/oversized nested list to ensure `TrieNode::decode` returns an `Err` rather than panicking.

### Proof of Concept
Construct a byte string whose outer RLP header is a valid 2-item list header (so `LEAF_OR_EXTENSION_LIST_LENGTH` branch triggers `rlp_list_element_length`), but where the first counted sub-item's header declares a `payload_length` larger than the bytes actually present after it, e.g.:
```
list_header (list=true, e.g. 0xc2..) followed by
  element_header claiming payload_length = N
  <fewer than N bytes actually present>
```
Feeding this via `TrieNode::decode(&mut bytes.as_ref())` (reached through `OrderedListWalker::get_trie_node` / any `TrieProvider::trie_node_by_hash` preimage lookup in the fault-proof program) causes `rlp_list_element_length` to either underflow `buf.len() - header.payload_length` or panic in `buf.advance(header.payload_length)`, crashing the calling process instead of returning a decode error. [1](#0-0) [4](#0-3)

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

**File:** crates/common/consensus/src/receipts/deposit.rs (L207-217)
```rust
impl<T: Decodable> RlpDecodableReceipt for DepositReceipt<T> {
    fn rlp_decode_with_bloom(buf: &mut &[u8]) -> alloy_rlp::Result<ReceiptWithBloom<Self>> {
        let header = Header::decode(buf)?;
        if !header.list {
            return Err(alloy_rlp::Error::UnexpectedString);
        }

        if buf.len() < header.payload_length {
            return Err(alloy_rlp::Error::InputTooShort);
        }

```

**File:** crates/common/consensus/src/transaction/eip8130/tx.rs (L140-147)
```rust
    fn decode_calls(buf: &mut &[u8]) -> alloy_rlp::Result<Vec<Vec<Call>>> {
        let header = Header::decode(buf)?;
        if !header.list {
            return Err(alloy_rlp::Error::UnexpectedString);
        }
        if buf.len() < header.payload_length {
            return Err(alloy_rlp::Error::InputTooShort);
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

**File:** crates/proof/mpt/src/list_walker.rs (L133-141)
```rust
    fn get_trie_node<T>(hash: T, fetcher: &F) -> OrderedListWalkerResult<TrieNode>
    where
        T: Into<B256>,
    {
        fetcher
            .trie_node_by_hash(hash.into())
            .map_err(|e| TrieNodeError::Provider(e.to_string()))
            .map_err(Into::into)
    }
```
