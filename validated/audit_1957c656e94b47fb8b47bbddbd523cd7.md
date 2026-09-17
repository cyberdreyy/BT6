Confirmed: preimage values are only validated by hash equality (keccak256/sha256 of the raw bytes must equal the key), with no size or structural limit on the preimage content, and the value is accepted as arbitrary bytes for any size an attacker chooses since a keccak256 preimage is simply "any bytes that hash to the requested digest." This is exactly the setup needed for the stack-overflow analog.

### Title
Unbounded recursive RLP decode of MPT `TrieNode` allows stack-overflow DoS of the fault-proof program via attacker-supplied preimage data - (File: `crates/proof/mpt/src/node.rs`)

### Summary
The Samba CVE (CVE-2020-10704) is a stack-overflow DoS caused by unbounded recursive parsing of an attacker-controlled request in an AD LDAP handler. The equivalent bug class exists in Base's fault-proof `TrieNode::decode` implementation, which recursively decodes RLP `Extension` nodes with no depth limit, driven entirely by attacker-supplied preimage bytes fetched through the fault-proof preimage oracle.

### Finding Description
`TrieNode::decode` in [1](#0-0)  dispatches on RLP list length: a 2-element list is treated as a leaf-or-extension node and routed to `try_decode_leaf_or_extension_payload`. That function, for the extension case, recursively calls `Self::decode(buf)` again on the remaining bytes with no depth counter or recursion-depth bound: [2](#0-1) .

This single `decode` call operates on one in-memory buffer — the recursion does not require separate oracle round-trips per level (only `Branch`/`Leaf` sub-cases involve `Box`-ed pointer nodes that get "blinded" as 32-byte hashes when correctly constructed, but the decoder itself never enforces that invariant on the way in). An attacker who controls a preimage blob supplied through the preimage oracle can therefore build one buffer containing thousands of nested 2-element RLP lists, each just a few bytes (`[path, nested_list]`), causing `Self::decode` to recurse thousands of times on a single call stack before returning.

This decoder is reachable in the fault-proof program through `TrieProvider::trie_node_by_hash`, which fetches the raw bytes from the oracle and calls `TrieNode::decode` directly: `OracleL1ChainProvider` [3](#0-2)  and `OracleL2ChainProvider` [4](#0-3) . Preimages are addressed by `PreimageKeyType::Keccak256`, meaning *any* byte string whose keccak256 digest matches the requested key is accepted as valid — there is no length or structural check on the content: [5](#0-4)  and [6](#0-5) . In an actual dispute game, a malicious participant supplying preimage data for the disputed state trie (or a malicious/compromised host feeding data to the fault-proof VM) fully controls these bytes, and only needs to find a keccak-preimage of the required 32-byte commitment matching their deeply-nested payload — which is trivial since the commitment itself is derived by hashing the attacker-chosen blob (the attacker picks both the blob and its hash simultaneously when constructing the malicious trie node during account/storage manipulation, or supplies it directly as `debug_dbGet`/oracle content in a permissionless dispute game).

### Impact Explanation
A crafted trie-node preimage with deep RLP extension-node nesting will drive `TrieNode::decode`'s recursion past the thread stack limit, crashing the fault-proof program (or TEE/zkVM witness-execution paths that reuse the same `TrieNode::decode`) with a stack overflow. Because the fault-proof program is the mechanism used to resolve dispute games and compute the provably-correct output root, a reliable crash here means a dispute-game participant can prevent the honest party's fault-proof program from completing execution over a maliciously-crafted state, resulting in denial of service of the dispute resolution process (node halt) — potentially causing incorrect game resolution if the honest challenger's program cannot produce a valid claim in the available time.

### Likelihood Explanation
This is straightforward to trigger: the preimage byte layout is entirely attacker-controlled RLP, requiring no privileged access — only the ability to supply data claimed to be a trie-node preimage. This is reachable by any dispute-game participant or via a malicious host serving the on-disk preimage store, both of which are explicitly in-scope reachable actors for the fault-proof program and MPT.

### Recommendation
Add an explicit recursion-depth counter/limit to `TrieNode::decode` (and its recursive callers `try_decode_leaf_or_extension_payload`, `open`, `insert`, `unblind`), rejecting RLP structures nested beyond the maximum possible real trie depth (64 nibbles for a 32-byte key, i.e., far fewer than the depths achievable with 2-element non-blinded lists). Alternatively, convert the recursive decode into an explicit iterative/stack-based algorithm bounded by an internal `Vec`, analogous to the `MAX_CBOR_NESTING_DEPTH` guard already used in `crates/proof/tee/registrar/src/cbor.rs`.

### Proof of Concept
Construct an RLP buffer of nested 2-item lists:
```
node_n = leaf: rlp([single_nibble_path, small_value])
node_i = rlp([single_nibble_path (extension prefix), node_{i+1}])  for i = 0..N
```
Feed `node_0`'s encoded bytes as the value returned by a mock/malicious `PreimageOracleClient::get` for the requested `PreimageKey::new_keccak256(keccak256(node_0_bytes))`. Call `TrieNode::decode(&mut node_0_bytes.as_slice())` (as `OracleL1ChainProvider::trie_node_by_hash` / `OracleL2ChainProvider::trie_node_by_hash` do) with `N` in the tens of thousands; observe the process crash with a stack overflow before returning, matching the `crates/utilities/cli/src/sigsegv.rs` SIGSEGV-on-stack-overflow signature already documented and tested elsewhere in this repo (`crates/utilities/cli/tests/sigsegv_test.rs`), confirming the fault-proof program itself has no built-in defense against this class of crash originating from decode logic.

### Citations

**File:** crates/proof/mpt/src/node.rs (L453-461)
```rust
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
