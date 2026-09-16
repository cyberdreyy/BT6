## Analog Found [1](#0-0) 

### Title
Unbounded recursive descent in `TrieNode::decode` allows stack-overflow DoS of the fault-proof program via a crafted node preimage - (File: crates/proof/mpt/src/node.rs)

### Summary
`TrieNode::decode` recursively decodes RLP-encoded Merkle-Patricia-Trie nodes with no depth limit, mirroring the CVE-2011-1755 bug class (unbounded recursive expansion during untrusted document parsing causing memory/CPU exhaustion). A dispute-game participant who supplies a crafted node preimage to the fault-proof program's key/value store can trigger unbounded recursion and crash the verifier process.

### Finding Description
`TrieNode::decode` dispatches on the RLP list length: a 2-element list is treated as a `Leaf`/`Extension` and, for the `Extension` case, immediately recurses into `Self::decode` on the inner payload via `try_decode_leaf_or_extension_payload`, and a 17-element list (`Branch`) recurses into `Vec::<Self>::decode` for each of the 16+1 children: [2](#0-1) [3](#0-2) 

Unlike other parsers in the same codebase — e.g. the Nitro attestation CBOR parser, which explicitly tracks and bounds recursion via `depth` and `MAX_CBOR_NESTING_DEPTH`: [4](#0-3) 

`TrieNode::decode` takes no depth parameter and enforces no recursion bound whatsoever. The only implicit constraint that "real" trie nodes obey — that a node longer than 32 encoded bytes must be blinded (hashed) rather than inlined — is enforced only by the *encoder* (`payload_length`/`blind()`), not by the decoder: [5](#0-4) 
A byte buffer handed to `decode()` is never checked against this rule, so an attacker-constructed preimage can chain arbitrarily many 2-element (`Extension`) RLP lists inside a single contiguous buffer, each just a few bytes, to produce deep recursion bounded only by the size of the preimage blob.

This decoder is fed directly by preimage data pulled from the untrusted host/oracle in the fault-proof program, where the only check performed is that the preimage's `keccak256` hash matches the requested key — not that its internal structure is well-formed or shallow: [6](#0-5) [7](#0-6) 

Since a dispute-game participant is free to choose which preimages are supplied to satisfy oracle requests (any bytes hashing to the correct commitment count as valid), this recursion is directly reachable by an adversarial party providing witness/preimage data for trie traversal (`TrieNode::open`/`unblind`), e.g. via `get_trie_account`/`storage` in the executor's `TrieDB`: [8](#0-7) 

### Impact Explanation
A crafted deeply-nested node preimage causes unbounded recursive stack growth in `TrieNode::decode`, leading to a stack overflow and process crash of the fault-proof program (host or on-chain equivalent) during trie traversal. This halts dispute-game execution for the affected claim, preventing the node from producing/verifying an output root — a node-halt / denial-of-service condition within the scope of accepted impacts.

### Likelihood Explanation
Likelihood is limited by the practical constraint that a valid `Extension`/`Branch` chain used in `open()`/`unblind()` must still terminate the path lookup, and the attacker (as a permissionless fault-dispute participant supplying arbitrary preimages) must find a preimage matching a requested hash. Because preimages are only validated by hash equality and never by internal structural depth, an attacker acting as the untrusted preimage source has full freedom to shape the byte layout as long as the hash matches — a property that's realistic in the host/oracle preimage-serving model.

### Recommendation
Add an explicit recursion-depth counter/limit to `TrieNode::decode` (and its callers `try_decode_leaf_or_extension_payload`/`Vec::<Self>::decode`), analogous to `MAX_CBOR_NESTING_DEPTH` in the Nitro CBOR parser, and reject decoding once a maximum plausible trie depth (e.g., 64–65, matching the 256-bit keccak key space) is exceeded.

### Proof of Concept
Construct a preimage blob consisting of thousands of nested 2-element RLP lists of the form `rlp([short_odd_extension_path_byte, rlp([...])])`, each nested one inside another, ending in a terminal `Leaf`. Feed this blob to `TrieNode::decode` (e.g. by having the preimage oracle serve it for a `keccak256`-matching key requested during `TrieDB::storage`/`basic`). The recursive `Self::decode` calls in `try_decode_leaf_or_extension_payload` will recurse once per nesting level with no depth check, exhausting the call stack and crashing the process before hash/authorization checks on higher-level semantics are ever reached.

### Citations

**File:** crates/proof/mpt/src/node.rs (L110-122)
```rust
    /// Blinds the [`TrieNode`].. Alternatively, if the [`TrieNode`] is a [`TrieNode::Blinded`] node
    /// already, its commitment is returned directly.
    pub fn blind(&self) -> B256 {
        match self {
            Self::Blinded { commitment } => *commitment,
            Self::Empty => EMPTY_ROOT_HASH,
            _ => {
                let mut rlp_buf = Vec::with_capacity(self.length());
                self.encode(&mut rlp_buf);
                keccak256(rlp_buf)
            }
        }
    }
```

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

**File:** crates/proof/tee/registrar/src/cbor.rs (L94-100)
```rust
    /// Decodes an item with `depth` enclosing containers already entered.
    pub fn read_at(bytes: &[u8], start: usize, depth: usize) -> PlannerResult<Self> {
        if depth > MAX_CBOR_NESTING_DEPTH {
            return Err(PlannerError::Cose(format!(
                "CBOR nesting exceeds maximum depth {MAX_CBOR_NESTING_DEPTH}"
            )));
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

**File:** crates/proof/zk/utils/src/witness/preimage_store.rs (L80-91)
```rust
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
```

**File:** crates/proof/executor/src/db/mod.rs (L304-335)
```rust
    fn storage(&mut self, address: Address, index: U256) -> Result<U256, Self::Error> {
        // Send a hint to the host to fetch the storage proof.
        self.hinter
            .hint_storage_proof(address, index, self.parent_block_header.number)
            .map_err(|e| TrieDBError::Provider(e.to_string()))?;

        // Fetch the account's storage root from the cache. If storage is being accessed, the
        // account should have been loaded into the cache by the `basic` method. If the account was
        // non-existing, the storage root will not be present.
        match self.storage_roots.get_mut(&address) {
            None => {
                // If the storage root for the account does not exist, return zero.
                Ok(U256::ZERO)
            }
            Some(storage_root) => {
                // Fetch the storage slot from the trie.
                let hashed_slot_key = keccak256(index.to_be_bytes::<32>().as_slice());
                match storage_root.open(&Nibbles::unpack(hashed_slot_key), &self.fetcher)? {
                    Some(slot_value) => {
                        // Decode the storage slot value.
                        let int_slot = U256::decode(&mut slot_value.as_ref())
                            .map_err(TrieNodeError::RLPError)?;
                        Ok(int_slot)
                    }
                    None => {
                        // If the storage slot does not exist, return zero.
                        Ok(U256::ZERO)
                    }
                }
            }
        }
    }
```
