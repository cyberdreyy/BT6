### Title
Unbounded recursion in `TrieNode::decode` allows stack-overflow denial of service against the fault-proof program - ([File: crates/proof/mpt/src/node.rs])

### Summary
`base-proof-mpt`'s `TrieNode::decode` (used by every `TrieProvider::trie_node_by_hash` implementation in the fault-proof program) recursively decodes RLP-encoded Merkle Patricia Trie nodes with no depth limit, mirroring the root cause of CVE-2020-28196 (MIT krb5's ASN.1 BER decoder recursing without a bound). A dispute-game participant who controls the L1/L2 preimage data supplied to the oracle-backed trie providers can craft a single trie-node preimage containing deeply nested, non-blinded `Extension`/`Branch` payloads that cause the decoder to recurse until the call stack is exhausted.

### Finding Description
`TrieNode::decode` dispatches on the RLP header and, for `Extension` nodes, recurses directly into `Self::decode(buf)` inside `try_decode_leaf_or_extension_payload`, and for `Branch` nodes decodes a `Vec<Self>` of 17 children, each of which can again be an `Extension`/`Branch` node: [1](#0-0) [2](#0-1) 

Crucially, only nodes whose RLP length is ≥ 32 bytes are "blinded" into a 32-byte hash reference that must be fetched separately via the oracle; nodes below that threshold remain **inlined verbatim** in the parent's RLP payload: [3](#0-2) [4](#0-3) 

This means a single preimage blob returned by the (untrusted, attacker-influenced) preimage oracle can encode a chain of many nested inline `Extension`/`Branch` nodes (each just a few bytes), all decoded in one call to `TrieNode::decode` with no recursion-depth check anywhere in the decode path — the same missing-recursion-bound bug class as CVE-2020-28196.

This decoder is invoked directly on untrusted preimage bytes in the fault-proof program's L1 and L2 trie providers: [5](#0-4) [6](#0-5) 

and by the trie account/storage-proof lookups used during block execution inside the fault-proof program: [7](#0-6) 

### Impact Explanation
Any party who can influence the preimage data ingested during fault-proof execution (e.g., a dispute-game participant supplying L1 header/receipt/transaction/state preimages, or L2 account/storage proof data derived from attacker-controlled L1 deposit/system-config inputs) can trigger unbounded recursion in `TrieNode::decode`, overflowing the stack and crashing/halting the fault-proof program (a permanent denial of service of the node process running the program, e.g. the MIPS/RISC-V FPVM or the native host). Because the program is core to computing/validating the proof's output root, this can prevent the correct provable output root from being produced, meeting the "node halt / wrong provable output root" criteria.

### Likelihood Explanation
The malformed data only needs to pass RLP header validation (a valid list header and element count of 2 or 17), which is straightforward to construct; no cryptographic preimage collision is required since blinding is size-based, not content-based. Reaching this code path requires the fault-proof/dispute-game machinery to decode a maliciously supplied preimage, which is within the documented threat model of the dispute-game participant.

### Recommendation
Add an explicit recursion-depth counter/limit to `TrieNode::decode` (and the `try_decode_leaf_or_extension_payload` extension path), returning a decode error once a maximum nesting depth (e.g., matching the maximum possible MPT depth of 64 nibbles) is exceeded, analogous to the `MAX_CBOR_NESTING_DEPTH` guard already used in `crates/proof/tee/registrar/src/cbor.rs`.

### Proof of Concept
Construct an RLP blob representing a chain of ~N nested 2-element list nodes (`Extension` nodes with a 1-nibble non-blinded pointer to another such node), each individually shorter than 32 bytes so no blinding hash boundary is introduced, terminating in a `Leaf`. Feed this blob as the preimage returned for a `Keccak256` preimage key requested by `OracleL1ChainProvider::trie_node_by_hash` or `OracleL2ChainProvider::trie_node_by_hash`. For sufficiently large N (bounded only by the buffer size, not by any decoder limit), calling `TrieNode::decode` on this blob overflows the call stack.

### Citations

**File:** crates/proof/mpt/src/node.rs (L439-461)
```rust
    fn try_decode_leaf_or_extension_payload(buf: &mut &[u8]) -> TrieNodeResult<Self> {
        // Decode the path and value of the leaf or extension node.
        let path = Bytes::decode(buf).map_err(TrieNodeError::RLPError)?;
        let Some(first_byte) = path.first() else {
            return Err(TrieNodeError::InvalidNodeType);
        };
        let first_nibble = first_byte >> NIBBLE_WIDTH;
        let first = match first_nibble {
            PREFIX_EXTENSION_ODD | PREFIX_LEAF_ODD => Some(first_byte & 0x0F),
            PREFIX_EXTENSION_EVEN | PREFIX_LEAF_EVEN => None,
            _ => return Err(TrieNodeError::InvalidNodeType),
        };

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

**File:** crates/proof/mpt/src/node.rs (L503-512)
```rust
    /// Returns the encoded length of the trie node, blinding it if it is longer than an encoded
    /// [B256] string in length.
    ///
    /// ## Returns
    /// - `usize` - The encoded length of the value, blinded if the raw encoded length is longer
    ///   than a [B256].
    fn blinded_length(&self) -> usize {
        let encoded_len = self.length();
        if encoded_len >= B256::ZERO.len() { B256::ZERO.length() } else { encoded_len }
    }
```

**File:** crates/proof/mpt/src/node.rs (L527-551)
```rust
            Self::Extension { prefix, node } => {
                // Encode the extension node's header, prefix, and pointer node.
                Header { list: true, payload_length }.encode(out);
                alloy_trie::nodes::encode_path_leaf(prefix, false).as_slice().encode(out);
                if node.length() >= B256::ZERO.len() {
                    let hash = node.blind();
                    hash.encode(out);
                } else {
                    node.encode(out);
                }
            }
            Self::Branch { stack } => {
                // In branch nodes, if an element is longer than 32 bytes in length, it is blinded.
                // Assuming we have an open trie node, we must re-hash the elements
                // that are longer than 32 bytes in length.
                Header { list: true, payload_length }.encode(out);
                for node in stack {
                    if node.length() >= B256::ZERO.len() {
                        let hash = node.blind();
                        hash.encode(out);
                    } else {
                        node.encode(out);
                    }
                }
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

**File:** crates/proof/proof/src/l1/chain_provider.rs (L125-142)
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
```

**File:** crates/proof/proof/src/l2/chain_provider.rs (L173-190)
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
```

**File:** crates/proof/executor/src/db/mod.rs (L132-154)
```rust
    pub fn get_trie_account(
        &mut self,
        address: &Address,
        block_number: u64,
    ) -> TrieDBResult<Option<TrieAccount>> {
        // Send a hint to the host to fetch the account proof.
        self.hinter
            .hint_account_proof(*address, block_number)
            .map_err(|e| TrieDBError::Provider(e.to_string()))?;

        // Fetch the account from the trie.
        let hashed_address_nibbles = Nibbles::unpack(keccak256(address.as_slice()));
        let Some(trie_account_rlp) = self.root_node.open(&hashed_address_nibbles, &self.fetcher)?
        else {
            return Ok(None);
        };

        // Decode the trie account from the RLP bytes.
        TrieAccount::decode(&mut trie_account_rlp.as_ref())
            .map_err(TrieNodeError::RLPError)
            .map_err(Into::into)
            .map(Some)
    }
```
