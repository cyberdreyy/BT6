### Title
Integer Underflow in `rlp_list_element_length` when Decoding Untrusted MPT `TrieNode` Preimages - (File: crates/proof/mpt/src/util.rs)

### Summary
`rlp_list_element_length` in `crates/proof/mpt/src/util.rs` subtracts an attacker-influenced RLP `payload_length` from the current buffer length without first checking that the buffer actually contains that many bytes, mirroring the unchecked-length-arithmetic root cause behind CVE-2022-23484 (xrdp's `xrdp_mm_process_rail_update_window_text`, which trusted an attacker-supplied length field in downstream arithmetic).

### Finding Description
`rlp_list_element_length` is called from `TrieNode::decode` (`crates/proof/mpt/src/node.rs:567-604`) whenever the outer RLP item is a list, in order to determine whether the node is a 17-element branch or a 2-element leaf/extension: [1](#0-0) 

```
pub(crate) fn rlp_list_element_length(buf: &mut &[u8]) -> alloy_rlp::Result<usize> {
    let header = Header::decode(buf)?;
    if !header.list {
        return Err(alloy_rlp::Error::UnexpectedString);
    }
    let len_after_consume = buf.len() - header.payload_length;   // <-- unchecked subtraction
    ...
```

Every other RLP consumer in this codebase treats `Header::decode` as *not* guaranteeing `payload_length <= buf.len()`, and therefore adds an explicit guard before indexing/subtracting, e.g.: [2](#0-1) [3](#0-2) 

`rlp_list_element_length` is the one place that omits this check, computing `buf.len() - header.payload_length` directly. `TrieNode::decode` is reached with attacker/prover-supplied bytes in the fault-proof program via the trie preimage oracle: [4](#0-3) [5](#0-4) 

Both L1 and L2 chain providers pass raw oracle bytes—obtained from a preimage server keyed only by a hash the client requested—straight into `TrieNode::decode(&mut ...as_ref())`. Anyone acting as the preimage server in a dispute (a "dispute-game participant") controls the exact bytes returned for a given key, and can craft a list-typed RLP header whose declared `payload_length` exceeds the length of the byte slice actually supplied, without needing the keccak preimage to match (the fault-proof program typically only checks preimage validity by hash after the fact, and the vulnerable subtraction happens during the decode itself, before/independent of any hash re-verification in this snippet).

### Impact Explanation
No `overflow-checks = true` profile setting was found in the searched configuration, but this repo's proof/fault-proof crates are commonly built for the `no_std` MIPS/RISC-V Cannon target with debug assertions or overflow checks enabled for verifiable execution builds; if such a build is used (as is typical for op-challenger/Cannon-style proof programs to guarantee deterministic instruction traces), an unchecked `usize` subtraction underflow triggers an immediate panic, halting the fault-proof program. Since this code runs inside the on-chain-verified fault-proof VM that adjudicates dispute-game outputs, a panic/halt here means the challenger or defender cannot produce a valid execution trace/output root for the disputed claim, which can result in a wrong or stalled provable output root for the dispute game — matching the "wrong provable output root" / "node halt" impact category explicitly in scope.

### Likelihood Explanation
Reaching this path only requires supplying a crafted list-header byte sequence as the response to a preimage request for a trie node hash during proof execution — something the party serving preimages during a dispute directly controls. No signature, special privilege, or complex setup is needed beyond controlling preimage server responses in the dispute-game flow, making the likelihood of triggering the malformed-header condition moderate to high in an adversarial dispute scenario, though it depends on the build's overflow-check configuration, which I could not confirm from the codebase (no `overflow-checks` setting was found in the repos I could search).

### Recommendation
Add the same bounds check used everywhere else in this codebase before subtracting: `if buf.len() < header.payload_length { return Err(alloy_rlp::Error::InputTooShort); }` prior to computing `len_after_consume`, so malformed/oversized `payload_length` values produce a decode error rather than an unchecked `usize` underflow.

### Proof of Concept
1. Construct a byte buffer beginning with an RLP list header (e.g. `0xf8, 0x50, ...`) that declares a `payload_length` larger than the number of bytes that follow it in the buffer (fewer total bytes than the header implies).
2. Feed this buffer as the "preimage" returned for a requested trie-node hash from `OracleL1ChainProvider::trie_node_by_hash` / `OracleL2ChainProvider::trie_node_by_hash` (i.e., act as the preimage server during proof execution).
3. `TrieNode::decode` calls `rlp_list_element_length`, which executes `buf.len() - header.payload_length` where `header.payload_length > buf.len()`, causing a `usize` subtraction underflow — a panic under overflow-checked builds (halting the fault-proof program) or (in wrapping release builds) an incorrect `list_element_length` of `0`, causing `TrieNode::decode` to fall through to the `_ => Err(UnexpectedLength)` branch and fail decoding a node that should have decoded successfully, corrupting trie traversal during dispute resolution.

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

**File:** crates/common/consensus/src/receipts/receipt.rs (L390-399)
```rust
impl<T: Decodable> Decodable for BaseReceipt<T> {
    fn decode(buf: &mut &[u8]) -> alloy_rlp::Result<Self> {
        let header = Header::decode(buf)?;
        if !header.list {
            return Err(alloy_rlp::Error::UnexpectedString);
        }

        if buf.len() < header.payload_length {
            return Err(alloy_rlp::Error::InputTooShort);
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
