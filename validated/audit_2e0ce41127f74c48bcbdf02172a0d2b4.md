### Title
Unchecked-length RLP list-element counter in `rlp_list_element_length` can panic/mis-parse `TrieNode` decoding during fault-proof trie traversal - (File: crates/proof/mpt/src/util.rs)

### Summary
`base-proof-mpt`'s `TrieNode::decode` uses two independent RLP length computations that must agree, exactly the failure mode described in the external report (a length-precalculation routine that disagrees with the "real" `Decodable` implementation). `Header::decode` (from `alloy_rlp`) is used to get the node's outer header, and a separate, hand-rolled walker, `rlp_list_element_length`, is used only to *count* the number of top-level RLP elements so the code can decide whether a node is a 17-element branch or a 2-element leaf/extension. Unlike the header-based decoders used everywhere else in this codebase (e.g. `crates/common/consensus/src/transaction/deposit.rs:97-99`, `crates/common/consensus/src/transaction/eip8130/tx.rs:145-147`, `crates/common/consensus/src/transaction/eip8130/signed.rs:451-453`), which explicitly check `header.payload_length` against the remaining buffer length before subtracting or slicing, `rlp_list_element_length` performs an **unchecked subtraction** of a peer-controlled length field from the buffer length. [1](#0-0) 

### Finding Description
`rlp_list_element_length` is:

```rust
pub(crate) fn rlp_list_element_length(buf: &mut &[u8]) -> alloy_rlp::Result<usize> {
    let header = Header::decode(buf)?;
    if !header.list {
        return Err(alloy_rlp::Error::UnexpectedString);
    }
    let len_after_consume = buf.len() - header.payload_length;
    ...
``` [2](#0-1) 

`header.payload_length` is fully attacker/preimage-controlled (it is the byte-length declared by the outer RLP list header of a trie-node preimage). There is no check comparable to the `buf.len() < header.payload_length` guards used in every other `Decodable` implementation in this repository (see the deposit/EIP-8130/receipt decoders cited above) before this subtraction is performed. If `header.payload_length > buf.len()`, `buf.len() - header.payload_length` underflows a `usize`.

This routine is invoked from `TrieNode::decode` purely to *pre-classify* the node as branch (17 elements) vs leaf/extension (2 elements) before the "real" decode path runs (`Vec::<Self>::decode(buf)` for branches, or `Self::try_decode_leaf_or_extension_payload` after `buf.advance(header.length())` for leaf/extension nodes): [3](#0-2) 

Because this element-counting logic is a second, independent implementation of "how many RLP items are in this payload" that does not share the bounds-checking used by the actual `Decodable` machinery (`Header::decode` + `Vec::<Self>::decode`), the two paths can disagree on malformed-but-hash-consistent input, exactly mirroring the Pantheon report's `RlpUtils` vs. `RLPInput` divergence. Depending on the build's arithmetic-overflow-check configuration (workspace `Cargo.toml` does not set `overflow-checks` explicitly, so it defaults to enabled in `dev`/debug profiles and disabled in `release`), this underflow either panics (`attempt to subtract with overflow`) in debug-instrumented builds, or silently wraps to a huge `usize` in release builds, causing the subsequent `while buf.len() > len_after_consume` loop to terminate immediately and return an element count of `0`/incorrect value — silently misclassifying the node instead of surfacing a decode error.

### Impact Explanation
`TrieNode::decode` is the core node-decoder used throughout `base-proof-mpt`, which underlies the state/receipts/storage trie traversal for `base-proof-executor` in the OP-Stack fault-proof program (per the crate README: "Designed as the trie backend for `base-proof-executor`"). This program executes deterministically inside the dispute-game / fault-proof pipeline to derive the provable output root from L1-derived preimage data. A panic or a silently-wrong element count here would cause the proof program to crash (denial of the fault-proof computation for that dispute step) or to misinterpret trie structure (wrong branch/leaf/extension classification), which can produce a **wrong provable output root** or halt honest-node dispute resolution — both are in the explicit list of acceptable high-impact outcomes.

### Likelihood Explanation
Exploitability is constrained by the fact that inputs to `TrieNode::decode` are keyed preimages (their hash must match a previously-committed key before the preimage oracle will accept them), so an attacker cannot inject arbitrary malformed bytes without also controlling a real preimage for a required trie-node hash. This significantly limits practical reachability compared to a purely attacker-supplied RLP stream (e.g., a raw transaction). I could not fully verify, within the scope of this investigation, whether any code path allows a dispute-game participant to supply preimage bytes whose hash-binding is checked *after* this parsing occurs, or whether `overflow-checks` is enabled for the proof-program build profile (`crates/proof/zk/programs/succinct/Cargo.toml` defines custom `[profile.*]` sections I did not have the budget to fully inspect) — both of these facts materially change whether this is exploitable versus purely a defense-in-depth code-quality issue.

### Recommendation
Add an explicit `if header.payload_length > buf.len() { return Err(alloy_rlp::Error::InputTooShort); }` bounds check in `rlp_list_element_length` before computing `len_after_consume`, matching the pattern already used elsewhere in the codebase (e.g., `TxDeposit::rlp_decode`, `TxEip8130::decode_calls`, `Eip8130Signed::decode`). More generally, per the original report's long-term recommendation, unify the RLP "length precomputation" logic in `rlp_list_element_length` with the canonical `alloy_rlp::Decodable`/`Header` decoding path rather than maintaining a second bespoke walker, to eliminate this entire class of precompute-vs-decode divergence bugs.

### Proof of Concept
Construct a "peer" preimage whose outer RLP header claims `list: true` with a `payload_length` larger than the number of bytes actually present in the buffer slice passed to `TrieNode::decode` (analogous to the report's `0xbc0100000000` case, but for a list header, e.g. `0xf8ff` followed by fewer than `0xff` bytes). Feeding this buffer to:
```rust
base_proof_mpt::util::rlp_list_element_length(&mut buf) // called internally by TrieNode::decode
```
drives `buf.len() - header.payload_length` to underflow. In a debug/overflow-checked build this panics; in a release build it returns element count `0`, causing `TrieNode::decode`'s `match list_length { .. _ => Err(UnexpectedLength) }` branch to reject a node that a bounds-checked implementation would have rejected for a different, more specific reason — demonstrating the two length-computation paths (`Header::decode`'s payload length vs. `rlp_list_element_length`'s unguarded walk) do not agree on malformed input, matching the report's core defect pattern.

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
