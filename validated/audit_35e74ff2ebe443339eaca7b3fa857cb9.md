### Title
Unbounded recursion in `TrieNode::decode` allows stack-overflow denial-of-service against the fault-proof program - (File: `crates/proof/mpt/src/node.rs`)

### Summary
The Ox gem CVE (CVE-2017-16229) is a stack-based overflow caused by unbounded/uncontrolled recursive parsing of attacker-supplied input in `sax_parse`/`read_from_str`. The analogous pattern in this codebase is `TrieNode`'s RLP `Decodable` implementation, which recurses into itself for nested `Extension` nodes and into `Vec::<Self>::decode` for `Branch` nodes, with no depth limit anywhere in the crate.

### Finding Description
`TrieNode::decode` (`crates/proof/mpt/src/node.rs:567-604`) dispatches on the RLP header: for a 17-element list it calls `Vec::<Self>::decode(buf)` (Branch), and for a 2-element list it calls `Self::try_decode_leaf_or_extension_payload`, which for an extension node calls `Self::decode(buf)` again (`crates/proof/mpt/src/node.rs:454-460`). Both paths recurse directly into `TrieNode::decode`/`Decodable::decode` with no depth counter or recursion-depth guard, unlike the CBOR parser in `crates/proof/tee/registrar/src/cbor.rs`, which explicitly tracks and enforces `MAX_CBOR_NESTING_DEPTH` (`crates/proof/tee/registrar/src/cbor.rs:13, 94-100`). [1](#0-0) [2](#0-1) [3](#0-2) 

`TrieNode::open` (`crates/proof/mpt/src/node.rs:153-182`) similarly recurses on `Branch`/`Extension`/`Blinded` variants without a depth bound, calling `unblind` → `fetcher.trie_node_by_hash` → `TrieNode::decode` on each nested/blinded step. [4](#0-3) 

This `TrieNode`/MPT code is used throughout the fault-proof program's trie providers — e.g. `OracleL1ChainProvider::trie_node_by_hash` and `DiskTrieNodeProvider::trie_node_by_hash` both call `TrieNode::decode` directly on preimage bytes obtained from an oracle/preimage server. [5](#0-4) [6](#0-5) 

### Impact Explanation
The fault-proof program is explicitly required to handle any preimage content served through its oracle interface without crashing, because that is the mechanism by which dispute-game participants supply data during proof execution. A stack overflow in `TrieNode::decode`/`open` would abort the fault-proof program's process (e.g. within the Cannon/MIPS or zkVM execution environment) instead of producing a deterministic output root, which can prevent honest participants from computing/submitting the correct output during a dispute game — a node-halt / wrong-provable-output condition in the fault-proof path explicitly listed as in-scope.

### Likelihood Explanation
Reaching deep nesting through *legitimate* Ethereum state data is constrained because real MPT nodes larger than 32 bytes are blinded (hashed) and require a genuine keccak preimage, limiting inline nesting depth per fetched node. However, the codebase provides no explicit depth guard, unlike the sibling CBOR parser which does bound recursion — the absence of any depth check is a code-quality/defense-in-depth gap rather than a proven, directly attacker-triggerable overflow with a single small payload. I was not able to fully verify within the available context whether any upstream caller enforces a maximum node size/depth before invoking `TrieNode::decode`, nor could I confirm the actual native stack budget in the various proving backends (SP1, Cannon, TEE) to determine the minimum nesting depth required to trigger an overflow.

### Recommendation
Add an explicit recursion-depth counter (mirroring `MAX_CBOR_NESTING_DEPTH` in `crates/proof/tee/registrar/src/cbor.rs`) to `TrieNode::decode` and `TrieNode::open`/`unblind`, bounded to the maximum possible MPT depth (64 nibbles), returning a decode error once exceeded instead of recursing further.

### Proof of Concept
Not independently reproducible from the indexed context alone — a full PoC would require constructing a nested RLP `Extension`/`Branch` blob (or a chain of preimages fetched via `trie_node_by_hash`) whose in-memory nesting depth exceeds the fault-proof program's native stack limit and feeding it through `OracleL1ChainProvider::trie_node_by_hash` / `DiskTrieNodeProvider::trie_node_by_hash`, then invoking `TrieNode::decode`. This would require running the actual fault-proof program/host to confirm the exact depth needed and whether upstream size checks prevent it — recommend a Devin session with codebase execution access to build and verify this PoC.

### Citations

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

**File:** crates/proof/executor/src/test_utils.rs (L326-339)
```rust
impl TrieProvider for DiskTrieNodeProvider {
    type Error = TestTrieNodeProviderError;

    fn trie_node_by_hash(&self, key: B256) -> Result<TrieNode, Self::Error> {
        TrieNode::decode(
            &mut self
                .kv_store
                .get(key)
                .map_err(|_| TestTrieNodeProviderError::PreimageNotFound)?
                .ok_or(TestTrieNodeProviderError::PreimageNotFound)?
                .as_slice(),
        )
        .map_err(TestTrieNodeProviderError::Rlp)
    }
```
