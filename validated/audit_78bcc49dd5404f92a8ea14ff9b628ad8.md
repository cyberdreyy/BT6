### Title
Unbounded recursive RLP trie-node decoding in the fault-proof MPT allows stack-overflow DoS via crafted preimage - (File: `crates/proof/mpt/src/node.rs`)

### Summary
`TrieNode::decode`, `TrieNode::open`, `TrieNode::insert`, and `OrderedListWalker::fetch_leaves` all recurse into child nodes with no depth limit. In the fault-proof program, trie-node preimages are supplied through the preimage oracle and only checked against a single keccak256 hash per fetched blob; there is no bound on the internal nesting depth encoded within one accepted preimage. A crafted preimage containing a long chain of nested `Extension`/`Branch` sub-nodes causes unbounded native-stack recursion during dispute-game program execution, analogous to CVE-2020-36368's stack overflow in a recursive-descent parser processing crafted input.

### Finding Description
`TrieNode::decode` recognizes any 2-element RLP list as a leaf-or-extension node and, for extension nodes, calls `Self::decode(buf)` recursively on the inline child payload without unblinding it via a separate hash-checked fetch: [1](#0-0) [2](#0-1) 

Because blinding to a separate hash-verified preimage only happens for RLP encodings longer than 32 bytes, an attacker can nest many small `Extension` nodes (each contributing only a few bytes: RLP list header + 1-byte path + child node) inside a single unblinded blob without ever triggering an additional keccak preimage lookup. The only hash check performed by the oracle path is on the single top-level preimage blob: [3](#0-2) [4](#0-3) 

That check only verifies `keccak256(preimage) == requested_hash`; it says nothing about how deeply the accepted bytes may nest internal (non-blinded) sub-structures, so an attacker who controls the underlying L1/L2 state that the dispute program is asked to derive/verify (e.g., a state/storage trie node reachable during account or storage proof traversal) can supply a single blob that decodes into thousands of nested `Extension` levels.

This same unbounded recursion pattern also exists in:
- `TrieNode::open`, which recurses through `Branch`/`Extension`/`Blinded` variants while walking a proof path: [5](#0-4) 
- `TrieNode::insert`, which recurses similarly when constructing tries: [6](#0-5) 
- `OrderedListWalker::fetch_leaves`, used to walk derivable lists (e.g., transactions/receipts) and which recurses into every `Branch`/`Extension` child, fetching and recursing further on any `Blinded` node: [7](#0-6) 

These functions are exercised by the fault-proof program via `TrieDB`/`TrieProvider` implementations that fetch and decode preimages during EVM state execution and derivation, e.g. `get_trie_account`: [8](#0-7) , and the EIP-2935 history lookup path that opens both a state trie and storage trie against attacker-influenced roots: [9](#0-8) .

### Impact Explanation
A dispute-game participant who can influence which L1/L2 trie preimages the fault-proof program will decode (by choosing what state a claim is made against, or by supplying preimage data for hints the honest challenger/host must resolve) can craft a deeply-nested-but-hash-valid single preimage blob. When the program decodes/opens/walks that node, it recurses natively without any depth guard, exhausting the call stack and crashing the fault-proof program process (host or on-chain-equivalent execution) with a segmentation fault/abort. This halts fault-dispute verification for the affected claim, which is a node/process halt during a security-critical path (dispute resolution), matching the allowed "fault-proof program and MPT" and "node halt" impact categories.

### Likelihood Explanation
Likelihood is limited by the fact that the recursion depth is bounded by how much nested structure can be packed into a single ≤ practical-preimage-size blob (no explicit preimage size cap was found in the reader path, `OracleReader::get`/`get_exact`), and by the attacker's ability to get such a node accepted as a legitimate part of the state/storage/history trie being derived. This requires the attacker to control or influence L1 data (e.g., a malicious/compromised sequencer-independent L1 state, or a chosen dispute claim) whose derivation causes the vulnerable decode/open/insert/fetch_leaves path to be invoked on the crafted structure. This is a real reachable path in the fault-proof program's core MPT logic but requires crafting non-trivial nested RLP.

### Recommendation
Add an explicit recursion/nesting depth limit to `TrieNode::decode`, `TrieNode::open`, `TrieNode::insert`, and `OrderedListWalker::fetch_leaves` (e.g., a `depth: usize` parameter capped at a small constant such as 64, matching the maximum realistic Ethereum trie depth), returning a decode error when exceeded instead of recursing further. Alternatively, convert these functions to iterative implementations using an explicit stack to avoid native stack growth entirely.

### Proof of Concept
1. Construct a "leaf-or-extension" RLP list of the form `rlp([path_byte, INNER])` where `INNER` is itself another such 2-element list, nested N times (each level adds ~3-4 bytes), keeping the total encoded length under 33 bytes is not required for inner levels since only the outermost fetched blob needs a hash match — only the single outer preimage needs `keccak256(blob) == requested_key`.
2. Register this blob as the preimage for a state/storage trie node hash that the fault-proof program is caused to fetch (e.g., via `trie_node_by_hash` on `OracleL1ChainProvider`, `crates/proof/proof/src/l1/chain_provider.rs:128-142`).
3. When the program calls `TrieNode::decode` (or `open`/`insert`/`OrderedListWalker::fetch_leaves`) on this blob, each nested level triggers another stack frame via `Self::decode(buf)` in `try_decode_leaf_or_extension_payload` (`crates/proof/mpt/src/node.rs:456`), with no depth check, until the process stack is exhausted and the program crashes.

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

**File:** crates/proof/mpt/src/node.rs (L293-304)
```rust
            Self::Branch { stack } => {
                // Follow the branch node to the next node in the path.
                let branch_nibble = path.get(0).ok_or(TrieNodeError::PathTooShort)? as usize;
                stack[branch_nibble].insert(&path.slice(BRANCH_NODE_NIBBLES..), value, fetcher)
            }
            Self::Blinded { .. } => {
                // If a blinded node is approached, reveal the node and continue the insertion
                // recursion.
                self.unblind(fetcher)?;
                self.insert(path, value, fetcher)
            }
        }
```

**File:** crates/proof/mpt/src/node.rs (L439-469)
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
            PREFIX_LEAF_EVEN | PREFIX_LEAF_ODD => {
                // Leaf node
                let value = Bytes::decode(buf).map_err(TrieNodeError::RLPError)?;
                Ok(Self::Leaf { prefix: unpack_path_to_nibbles(first, path[1..].as_ref()), value })
            }
            _ => Err(TrieNodeError::InvalidNodeType),
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

**File:** crates/proof/host/src/handler.rs (L1169-1189)
```rust
        HintType::L2StateNode => {
            if hint.data.len() != 32 {
                return Err(HostError::InvalidHintDataLength);
            }

            let hash: B256 = hint.data.as_ref().try_into()?;

            error!(node_hash = %hash, "debug_executePayload failed to return a complete witness");

            let preimage: Bytes = providers.l2.client().request("debug_dbGet", &[hash]).await?;
            let actual_hash = keccak256(preimage.as_ref());
            if actual_hash != hash {
                return Err(HostError::StateNodePreimageHashMismatch {
                    expected: hash,
                    actual: actual_hash,
                });
            }

            let mut kv_write_lock = kv.write().await;
            kv_write_lock.set(PreimageKey::new_keccak256(*hash).into(), preimage.into())?;
        }
```

**File:** crates/proof/mpt/src/list_walker.rs (L86-129)
```rust
    /// Traverses a [`TrieNode`], returning all values of child [`TrieNode::Leaf`] variants.
    fn fetch_leaves(
        trie_node: &TrieNode,
        fetcher: &F,
    ) -> OrderedListWalkerResult<VecDeque<(Bytes, Bytes)>> {
        match trie_node {
            TrieNode::Branch { stack } => {
                let mut leaf_values = VecDeque::with_capacity(stack.len());
                for item in stack {
                    match item {
                        TrieNode::Blinded { commitment } => {
                            // If the string is a hash, we need to grab the preimage for it and
                            // continue recursing.
                            let trie_node = Self::get_trie_node(commitment.as_ref(), fetcher)?;
                            leaf_values.append(&mut Self::fetch_leaves(&trie_node, fetcher)?);
                        }
                        TrieNode::Empty => { /* Skip over empty nodes, we're looking for values. */
                        }
                        item => {
                            // If the item is already retrieved, recurse on it.
                            leaf_values.append(&mut Self::fetch_leaves(item, fetcher)?);
                        }
                    }
                }
                Ok(leaf_values)
            }
            TrieNode::Leaf { prefix, value } => {
                Ok(vec![(prefix.to_vec().into(), value.clone())].into())
            }
            TrieNode::Extension { node, .. } => {
                // If the node is a hash, we need to grab the preimage for it and continue
                // recursing. If it is already retrieved, recurse on it.
                match node.as_ref() {
                    TrieNode::Blinded { commitment } => {
                        let trie_node = Self::get_trie_node(commitment.as_ref(), fetcher)?;
                        Ok(Self::fetch_leaves(&trie_node, fetcher)?)
                    }
                    node => Ok(Self::fetch_leaves(node, fetcher)?),
                }
            }
            TrieNode::Empty => Ok(VecDeque::new()),
            _ => Err(TrieNodeError::InvalidNodeType.into()),
        }
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

**File:** crates/proof/proof/src/eip2935.rs (L50-62)
```rust
    // Fetch the trie account for the history accumulator.
    let mut state_trie = TrieNode::new_blinded(header.state_root);
    let account_key = Nibbles::unpack(HASHED_HISTORY_STORAGE_ADDRESS);
    let raw_account = state_trie.open(&account_key, provider)?.ok_or(TrieNodeError::KeyNotFound)?;
    let account =
        TrieAccount::decode(&mut raw_account.as_ref()).map_err(OracleProviderError::Rlp)?;

    // Fetch the storage slot value from the account.
    let mut storage_trie = TrieNode::new_blinded(account.storage_root);
    let slot_key = Nibbles::unpack(keccak256(U256::from(slot).to_be_bytes::<32>()));
    let slot_value = storage_trie.open(&slot_key, provider)?.ok_or(TrieNodeError::KeyNotFound)?;

    B256::decode(&mut slot_value.as_ref()).map_err(OracleProviderError::Rlp)
```
