### Title
Integer underflow in `rlp_list_element_length` when decoding untrusted MPT proof nodes - (File: crates/proof/mpt/src/util.rs)

### Summary
The CVE describes an integer underflow computed from an attacker-controlled length field that is not validated against the actual remaining buffer size before being used in a subtraction, leading to memory corruption in the C implementation. The closest structural analog in this repo is `rlp_list_element_length`, which subtracts an RLP header's `payload_length` from the buffer's remaining length without first checking that `payload_length` does not exceed the buffer, unlike every other RLP decode call-site in this codebase.

### Finding Description
`rlp_list_element_length` decodes an RLP list header and then computes: [1](#0-0) 
`let len_after_consume = buf.len() - header.payload_length;` with no prior check that `header.payload_length <= buf.len()`.

Every other RLP list/consumer in this codebase that reads a `Header` explicitly guards against this before subtracting or slicing, e.g.: [2](#0-1) [3](#0-2) [4](#0-3) 

`rlp_list_element_length` lacks this bound check. It is invoked directly from `TrieNode::decode`, the RLP decoder for Merkle-Patricia-Trie nodes used by the fault-proof program's MPT implementation: [5](#0-4) 

`TrieNode` data is untrusted: it is fetched from the preimage oracle / L1 witness data during fault-proof execution (`trie_node_by_hash` in `TrieProvider`), i.e., attacker-influenced input in the fault-proof program and MPT path explicitly listed as in-scope.

### Impact Explanation
If a malicious/malformed trie-node preimage declares an RLP list `payload_length` larger than the bytes actually remaining in the buffer, `buf.len() - header.payload_length` underflows. In a debug build this triggers an arithmetic-overflow panic (crash/DoS of the fault-proof program, i.e., a node/proof-verification halt). In a release build (where Rust disables overflow checks by default and this workspace's `Cargo.toml` does not appear to enable `overflow-checks = true`, based on the search performed), the subtraction silently wraps to a value near `usize::MAX`, so the subsequent `while buf.len() > len_after_consume` loop never executes and `list_element_length` is returned as `0`. In `TrieNode::decode`, a `list_length` of `0` does not match `BRANCH_LIST_LENGTH` (17) or `LEAF_OR_EXTENSION_LIST_LENGTH` (2), so decoding falls through to `Err(UnexpectedLength)`. Because `alloy_rlp::Buf::advance` performs its own bounds checking (it panics rather than reading out of bounds), I could not confirm a path from this underflow to actual out-of-bounds memory access or heap corruption analogous to the CVE — only a panic (in debug) or a benign decode failure (in release).

### Likelihood Explanation
Reachability is plausible: the fault-proof program consumes attacker/L1-influenced preimage data through this exact decode path, and no other guard in the surrounding code prevents an oversized `payload_length` from reaching the subtraction. However, I was unable to verify from the available index whether the crate's build profile enables `overflow-checks`, nor could I trace a concrete downstream unsafe/unchecked slice operation that would turn the wraparound into a heap overflow (as in the original CVE) rather than a checked panic inside `alloy_rlp`'s own bounds-checked `Buf` implementation.

### Recommendation
Add an explicit bounds check before the subtraction, mirroring the pattern used everywhere else in the codebase, e.g.:
```rust
if header.payload_length > buf.len() {
    return Err(alloy_rlp::Error::InputTooShort);
}
let len_after_consume = buf.len() - header.payload_length;
```

### Proof of Concept
Construct a malicious RLP-encoded byte sequence presented as a trie-node preimage where the outer list header declares a `payload_length` larger than the number of bytes that follow it (e.g., a list header claiming ~0x1000 bytes of payload but supplying only a few trailing bytes), and feed it through `TrieNode::decode` (e.g., via a custom `TrieProvider::trie_node_by_hash` fixture returning this crafted bytes value). This drives execution into `rlp_list_element_length`, where `header.payload_length > buf.len()` triggers the unchecked subtraction — a debug build panics with "attempt to subtract with overflow"; I was not able to fully verify the release-mode consequence beyond the benign `0`/`Err(UnexpectedLength)` outcome described above given the tools available.

### Citations

**File:** crates/proof/mpt/src/util.rs (L63-68)
```rust
pub(crate) fn rlp_list_element_length(buf: &mut &[u8]) -> alloy_rlp::Result<usize> {
    let header = Header::decode(buf)?;
    if !header.list {
        return Err(alloy_rlp::Error::UnexpectedString);
    }
    let len_after_consume = buf.len() - header.payload_length;
```

**File:** crates/common/consensus/src/transaction/deposit.rs (L90-99)
```rust
    pub fn rlp_decode(buf: &mut &[u8]) -> alloy_rlp::Result<Self> {
        let header = Header::decode(buf)?;
        if !header.list {
            return Err(alloy_rlp::Error::UnexpectedString);
        }
        let remaining = buf.len();

        if header.payload_length > remaining {
            return Err(alloy_rlp::Error::InputTooShort);
        }
```

**File:** crates/common/consensus/src/receipts/deposit.rs (L209-216)
```rust
        let header = Header::decode(buf)?;
        if !header.list {
            return Err(alloy_rlp::Error::UnexpectedString);
        }

        if buf.len() < header.payload_length {
            return Err(alloy_rlp::Error::InputTooShort);
        }
```

**File:** crates/consensus/protocol/src/batch/tx_data/eip8130.rs (L201-207)
```rust
impl Decodable for SpanBatchEip8130TransactionData {
    fn decode(buf: &mut &[u8]) -> alloy_rlp::Result<Self> {
        let header = Header::decode(buf)?;
        if !header.list {
            return Err(alloy_rlp::Error::UnexpectedString);
        }
        let started = buf.len();
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
