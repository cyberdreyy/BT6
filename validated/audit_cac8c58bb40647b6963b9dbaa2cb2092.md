### Title
Unbounded recursive `TrieNode` decoding allows stack-overflow crash of the fault-proof program - ([File: crates/proof/mpt/src/node.rs])

### Summary
`TrieNode::decode` recursively decodes RLP-encoded Merkle Patricia Trie nodes with no depth limit, unlike the codebase's own CBOR parser (`NitroCose`/`CborItem`) which explicitly enforces `MAX_CBOR_NESTING_DEPTH` on every recursive descent [1](#0-0) . `TrieNode::decode` has no analogous bound: an `Extension` node's inner pointer can be a raw, non-blinded sub-node (RLP payload < 32 bytes) which is decoded via a direct recursive call to `Self::decode`/`try_decode_leaf_or_extension_payload`, with no depth counter and no cap on nesting [2](#0-1) [3](#0-2) .

### Finding Description
An Extension node is encoded as `rlp([encoded_path, node])`, and when `node`'s encoded length is under 32 bytes it is embedded directly rather than referenced by hash [4](#0-3) . On decode, `try_decode_leaf_or_extension_payload` calls `Self::decode(buf)` recursively for the inner node with zero depth tracking [5](#0-4) . Because each nested Extension header only costs a few bytes, an attacker can pack thousands of nested Extension layers into one preimage blob and trigger thousands of stack frames from a single `TrieNode::decode` call — the exact "endless expansion via nested constructs" bug class in CVE-2018-1000886, applied to RLP/MPT decoding instead of NASM macro expansion.

This decoder is on the fault-proof program's critical path: `OracleL1ChainProvider::trie_node_by_hash` and the L2 executor's trie-DB both call `TrieNode::decode` directly on preimage-oracle-supplied bytes with no separate depth/structure validation before recursing [6](#0-5) [7](#0-6) . The preimage oracle protocol itself performs no structural validation of the returned bytes beyond a length prefix — the host writes whatever bytes correspond to the requested key [8](#0-7) . Additionally, `TrieNode::open` and `TrieNode::insert` recurse per trie level through `Extension`/`Branch`/`Blinded::unblind` without any depth guard, compounding the same unbounded-recursion pattern across chained blinded lookups [9](#0-8) [10](#0-9) .

### Impact Explanation
A stack overflow in the fault-proof program (the component that recomputes/verifies the L2 state transition to produce or challenge an output root in the dispute game) can crash the process performing verification. Because the crash is deterministic on the crafted node blob, it can be used to reliably prevent an honest challenger or defender from completing verification of a specific claim within a dispute game, or to crash node processes that resolve `eth_getProof`/state-proof requests that reach `TrieNode::decode` on attacker-influenced data. This maps to the "node halt" / "wrong provable output root" impact class for the fault-proof program in scope.

### Likelihood Explanation
Medium. Exploiting this via genuinely-hashed preimages (where an attacker must supply bytes whose keccak256 matches a hash already committed in real trie data) constrains the attacker to real trie structures, whose depth is naturally bounded (≤64 nibbles for state/storage tries because keys are keccak-hashed). However, the un-blinded/embedded short-node path does not require the nested structure to correspond to any real commitment beyond the outermost preimage hash — the *inner* layers are embedded raw bytes chosen entirely by whoever supplies that one preimage, so nesting depth is bounded only by the preimage's byte size divided by the minimal per-level overhead (a handful of bytes), not by real trie depth. Any component that decodes such preimages without pre-validating structure (dispute-game preimage upload, `eth_getProof`-style state-proof consumers, TEE/zk provers reading preimages) is exposed.

### Recommendation
Add an explicit recursion/nesting-depth counter to `TrieNode::decode`, `TrieNode::open`, `TrieNode::insert`, and `TrieNode::unblind`, mirroring the `MAX_CBOR_NESTING_DEPTH` pattern already used in `crates/proof/tee/registrar/src/cbor.rs`. Reject decode/traversal once nesting exceeds the maximum structurally valid MPT depth (64 nibble levels for hashed-key tries, plus a small margin for embedded short nodes), returning an error instead of recursing further.

### Proof of Concept
Conceptually: construct a byte blob `B` where `B` is an RLP list `[path, B']`, and `B'` is itself `[path, B'']`, nested N times, each inner node kept under 32 bytes so it is embedded rather than blinded. Register `keccak256(B)` as a preimage key with the preimage oracle (this only requires B to be a valid preimage of its own hash, which any attacker can construct locally) and cause any of `TrieNode::decode`, `.open()`, or `.insert()` to be invoked on it. Because there is no depth cap, decoding recurses N times, consuming one stack frame per level; a sufficiently large N (bounded only by the preimage size limit, not the true trie structure) drives the process past its stack limit and crashes it with SIGSEGV/stack overflow, exactly analogous to the NASM `stdscan.c` endless macro-expansion crash in the referenced CVE.

Note: I could not find a numeric preimage-size cap for the oracle channel in the indexed portion of `crates/proof/preimage/src/oracle.rs`/`crates/proof/preimage/src/traits.rs`; confirming the exact maximum preimage size (and thus the maximum achievable nesting depth) would require checking channel/host-side size limits not fully covered by the current index — flagged as an open item for further verification in a full Devin session.

### Citations

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

**File:** crates/proof/mpt/src/node.rs (L153-182)
```rust
    pub fn open<'a, F: TrieProvider>(
        &'a mut self,
        path: &Nibbles,
        fetcher: &F,
    ) -> TrieNodeResult<Option<&'a mut Bytes>> {
        match self {
            Self::Branch { stack } => {
                let branch_nibble = path.get(0).ok_or(TrieNodeError::PathTooShort)? as usize;
                stack
                    .get_mut(branch_nibble)
                    .map(|node| node.open(&path.slice(BRANCH_NODE_NIBBLES..), fetcher))
                    .unwrap_or(Ok(None))
            }
            Self::Leaf { prefix, value } => Ok((path == prefix).then_some(value)),
            Self::Extension { prefix, node } => {
                if path.slice(..prefix.len()) == *prefix {
                    // Follow extension branch
                    node.unblind(fetcher)?;
                    node.open(&path.slice(prefix.len()..), fetcher)
                } else {
                    Ok(None)
                }
            }
            Self::Blinded { .. } => {
                self.unblind(fetcher)?;
                self.open(path, fetcher)
            }
            Self::Empty => Ok(None),
        }
    }
```

**File:** crates/proof/mpt/src/node.rs (L249-254)
```rust
            Self::Extension { prefix, node } => {
                let shared_extension_nibbles = path.common_prefix_length(prefix);
                if shared_extension_nibbles == prefix.len() {
                    node.insert(&path.slice(shared_extension_nibbles..), value, fetcher)?;
                    return Ok(());
                }
```

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

**File:** crates/proof/mpt/src/node.rs (L527-537)
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

**File:** crates/proof/proof/src/l1/chain_provider.rs (L128-141)
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
```

**File:** crates/proof/executor/src/db/mod.rs (L123-153)
```rust
    /// Fetches the [`TrieAccount`] of an account from the trie DB.
    ///
    /// ## Takes
    /// - `address`: The address of the account.
    ///
    /// ## Returns
    /// - `Ok(Some(TrieAccount))`: The [`TrieAccount`] of the account.
    /// - `Ok(None)`: If the account does not exist in the trie.
    /// - `Err(_)`: If the account could not be fetched.
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
```

**File:** crates/proof/preimage/src/oracle.rs (L115-136)
```rust
    async fn next_preimage_request<F>(&self, fetcher: &F) -> Result<(), PreimageOracleError>
    where
        F: PreimageFetcher + Send + Sync,
    {
        // Read the preimage request from the client, and throw early if there isn't any.
        let mut buf = [0u8; 32];
        self.channel.read_exact(&mut buf).await?;
        let preimage_key = PreimageKey::try_from(buf)?;

        trace!(target: "oracle_server", key = %preimage_key, "Fetching preimage");

        // Fetch the preimage value from the preimage getter.
        let value = fetcher.get_preimage(preimage_key).await?;

        // Write the length as a big-endian u64 followed by the data.
        self.channel.write(value.len().to_be_bytes().as_ref()).await?;
        self.channel.write(value.as_ref()).await?;

        trace!(target: "oracle_server", key = %preimage_key, "Successfully wrote preimage data");

        Ok(())
    }
```
