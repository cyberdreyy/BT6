This confirms the finding: preimage validation only checks `keccak256(value) == key` (the hash-preimage relationship), with no structural/depth validation before `TrieNode::decode` recurses. In the fault-proof program, both `OracleL1ChainProvider::trie_node_by_hash` and `OracleL2ChainProvider::trie_node_by_hash` call `TrieNode::decode` directly on oracle-supplied bytes, and `crates/proof/mpt/src/node.rs`'s `Decodable for TrieNode` recurses into `try_decode_leaf_or_extension_payload` → `Self::decode(buf)` for every nested `Extension` node with **no depth limit**, unlike the CBOR parser in `crates/proof/tee/registrar/src/cbor.rs` which explicitly enforces `MAX_CBOR_NESTING_DEPTH`.

### Title
Unbounded recursive `TrieNode` RLP decoding enables stack-overflow DoS of the fault-proof program via crafted preimages - (File: `crates/proof/mpt/src/node.rs`)

### Summary
`TrieNode::decode` (the `Decodable` impl) recursively decodes nested inline `Extension`/`Leaf` payloads with no bound on nesting depth, and this decoder is invoked directly on preimage-oracle-supplied bytes throughout the fault-proof program's stateless trie access path, allowing a malicious dispute-game participant (or malicious/compromised preimage host) to supply a crafted preimage that overflows the native stack and crashes the proof program.

### Finding Description
`TrieNode::decode` in [1](#0-0)  peeks the RLP header and, for a 2-element list, calls `try_decode_leaf_or_extension_payload`, which for the `Extension` prefix case recursively invokes `Self::decode(buf)` on the remaining bytes to decode the child node: [2](#0-1) . There is no depth counter or recursion-depth guard anywhere in this decode path, unlike the project's own CBOR parser (`crates/proof/tee/registrar/src/cbor.rs`), which explicitly tracks and rejects nesting beyond `MAX_CBOR_NESTING_DEPTH`.

This `TrieNode::decode` is the entry point used to interpret arbitrary bytes fetched from the untrusted preimage oracle in the fault-proof program's chain providers:
- `OracleL1ChainProvider::trie_node_by_hash` decodes oracle bytes directly: [3](#0-2) 
- `OracleL2ChainProvider::trie_node_by_hash` does the same for L2 state: [4](#0-3) 

Preimage validation only checks that `keccak256(value)` matches the requested key (a simple hash-preimage relationship), as seen in the TEE oracle and zk witness store: [5](#0-4)  and [6](#0-5) . Nothing constrains the *structure* of the bytes beyond that hash check — an attacker who controls what bytes get associated with a claimed hash (e.g. a dishonest party populating the preimage oracle/KV store during a dispute, or a malicious/compromised online host backend in `crates/proof/host/src/backend/online.rs`) can freely construct a single preimage blob consisting of thousands of nested inline `Extension` node headers. Each nesting level costs only a few bytes of RLP overhead (a short list header plus a 1–2 byte path string), so a preimage of a few hundred KB to a few MB can encode tens of thousands of recursion levels — enough to blow the stack of the fault-proof program (native executor, TEE enclave, or zkVM guest), all of which invoke this same `TrieNode::decode` function via `TrieProvider`.

This is directly analogous to CVE-2021-46238: a recursive tree-node accessor (`gf_node_get_name` walking GPAC's scenegraph) that lacks depth bounding and can be driven into a stack-overflow DoS by an attacker-supplied, deeply nested structure. Here, `TrieNode::decode`'s recursive walk over attacker-influenced RLP-encoded MPT nodes plays the same role.

### Impact Explanation
A crafted preimage can crash the fault-proof program (native `base-proof-*` executor, the SP1/Succinct zkVM guest, or the AWS Nitro TEE enclave) with a stack overflow before it produces any output. This halts proof/witness generation for the affected claim, preventing the honest party from computing or defending the correct output root within the dispute window — a concrete denial-of-service against the node's ability to serve a correct, provable output root during fault-proof resolution, matching the "wrong/absent provable output root" and "node halt" impact categories called out as in-scope.

### Likelihood Explanation
The bug is reachable via the officially in-scope "fault-proof program and MPT" path. It requires only that a party controlling preimage content (dispute-game challenger/defender providing conflicting data, or a malicious/compromised host backend that serves preimages, cf. `OnlineHostBackend::get_preimage` in `crates/proof/host/src/backend/online.rs`) is able to supply a preimage blob for a hash it controls. Because preimage acceptance only checks `keccak256(value) == key` and not decode-time structural bounds, constructing the malicious nested-extension blob is straightforward and requires no cryptographic breaks — likelihood is high once such a path to inject/choose preimage bytes exists in the trust model of the dispute game or a compromised/faulty online backend.

### Recommendation
Add an explicit recursion/nesting-depth limit to `TrieNode::decode` (and its recursive helpers `open`, `insert`, `delete`, `collapse_if_possible`, `unblind`), analogous to `MAX_CBOR_NESTING_DEPTH` in `crates/proof/tee/registrar/src/cbor.rs`, rejecting inputs that exceed the maximum possible legitimate MPT depth (bounded by the fixed 64-nibble key length for account/storage tries, plus a small safety margin) before recursing further. Alternatively, convert the decoder to an iterative/explicit-stack implementation to remove the native stack-depth dependency entirely.

### Proof of Concept
Construct a byte string `preimage` consisting of `N` nested RLP-encoded 2-element lists, each representing an `Extension` node whose "path" is a single-nibble string with the extension prefix nibble (e.g. `0x00`) and whose second element is the next nested list (rather than a blinded 32-byte commitment), terminated by a minimal `Leaf`/`Empty` node. For sufficiently large `N` (tens of thousands, achievable within a preimage of a few hundred KB–few MB), feed this as the response to `oracle.get(PreimageKey::new(keccak256(preimage), Keccak256))` for a hash referenced by `OracleL1ChainProvider::trie_node_by_hash` / `OracleL2ChainProvider::trie_node_by_hash`. Calling `TrieNode::decode(&mut preimage.as_ref())` will recurse `N` times through `try_decode_leaf_or_extension_payload` → `Self::decode`, overflowing the stack and crashing the fault-proof program process/enclave/zkVM guest.

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

**File:** crates/proof/tee/nitro-enclave/src/oracle.rs (L57-77)
```rust
    fn check_preimage(key: &PreimageKey, value: &[u8]) -> crate::Result<()> {
        let expected_hash: Option<[u8; 32]> = match key.key_type() {
            PreimageKeyType::Keccak256 => Some(keccak256(value).0),
            PreimageKeyType::Sha256 => Some(sha2::Sha256::digest(value).into()),
            // Blob keys are `keccak256(commitment ++ z)` and precompile keys are
            // `keccak256(address ++ input)` — neither can be re-derived from the
            // stored value alone, so we skip verifying them here and instead verify them
            // during derivation.
            PreimageKeyType::Local
            | PreimageKeyType::GlobalGeneric
            | PreimageKeyType::Blob
            | PreimageKeyType::Precompile => None,
        };

        if let Some(hash) = expected_hash
            && key != &PreimageKey::new(hash, key.key_type())
        {
            return Err(NitroError::InvalidPreimage(*key));
        }
        Ok(())
    }
```

**File:** crates/proof/zk/utils/src/witness/preimage_store.rs (L79-92)
```rust
/// Check that the preimage matches the expected hash.
pub fn check_preimage(key: &PreimageKey, value: &[u8]) -> PreimageOracleResult<()> {
    if let Some(expected_hash) = match key.key_type() {
        PreimageKeyType::Keccak256 => Some(keccak256(value).0),
        PreimageKeyType::Sha256 => Some(sha2::Sha256::digest(value).into()),
        PreimageKeyType::Local | PreimageKeyType::GlobalGeneric => None,
        PreimageKeyType::Precompile => unimplemented!("Precompile not supported in zkVM"),
        PreimageKeyType::Blob => unreachable!("Blob keys validated in blob witness"),
    } && key != &PreimageKey::new(expected_hash, key.key_type())
    {
        return Err(PreimageOracleError::InvalidPreimageKey);
    }
    Ok(())
}
```
