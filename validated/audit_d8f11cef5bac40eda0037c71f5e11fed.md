### Title
Unbounded recursive MPT `TrieNode` decoding in the fault-proof program allows stack-exhaustion crash from attacker-crafted L1 preimages - (File: crates/proof/mpt/src/node.rs)

### Summary
`base-proof-mpt`'s `TrieNode` implements `Decodable` with an implicit recursive descent: a `Branch` node decodes via `Vec::<Self>::decode` (which recurses into each of the up to 17 children) and an `Extension` node's `try_decode_leaf_or_extension_payload` recursively calls `Self::decode` on its child pointer. No recursion-depth (or node-count) bound is enforced anywhere in this decode path, unlike the CBOR parser used elsewhere in the codebase (`crates/proof/tee/registrar/src/cbor.rs`), which explicitly tracks `depth` and rejects input past `MAX_CBOR_NESTING_DEPTH`.

### Finding Description
`TrieNode::decode` [1](#0-0)  dispatches on RLP list length: a 17-element list recurses via `Vec::<Self>::decode(buf)` [2](#0-1) , and a 2-element list is routed to `try_decode_leaf_or_extension_payload`, which for an extension node recursively calls `Self::decode(buf)` on the nested node [3](#0-2) . Neither path tracks or bounds recursion depth.

This decoder is invoked directly on untrusted preimages fetched from an L1 (or L2) RPC endpoint during fault-proof derivation. `OracleL1ChainProvider::trie_node_by_hash` decodes a preimage fetched by hash into a `TrieNode` with no depth checking [4](#0-3) , and this is used by `OrderedListWalker` to walk receipt/transaction tries during derivation of `receipts_by_hash` / `block_info_and_transactions_by_hash`, which are core to processing attacker-controlled L1 deposit/system-config data during derivation [5](#0-4) . The `OrderedListWalker::fetch_leaves` traversal is also unboundedly recursive over `Branch`/`Extension` chains [6](#0-5) .

Crucially, the preimage a `TrieProvider` returns for a given hash is only validated by the *outer* keccak256 commitment (the hash key used to fetch it); the RLP *content* of that single node is fully attacker-controlled in shape as long as it hashes to the expected key when a real proof is constructed, but nested/child-node blobs referenced from within an "opened" node are fetched on demand via further hash lookups, each of which is decoded independently and recursively unblinded on subsequent `open()`/`unblind()` calls in `TrieNode` [7](#0-6) . A malicious L1 node (or a party that can influence what the untrusted RPC endpoint used by the host program returns for `debug_dbGet`/`debug_getRawReceipts`) can serve a deeply nested chain of extension/branch RLP blobs. Since the host program (`crates/proof/host/src/handler.rs`) fetches and stores raw preimages/receipts directly from an external L1 RPC via `debug_getRawReceipts`/`debug_dbGet` without any recursion-depth validation before storing/serving them to the client program [8](#0-7) , a sufficiently deep synthetic RLP nesting (well beyond the ~64-nibble depth of a legitimate hexary trie) forces the recursive `TrieNode::decode`/`fetch_leaves`/`unblind` call chains to consume unbounded native stack space in the fault-proof program (host or zkVM client), analogous to the qpdf `QPDFObjectHandle::parseInternal` infinite-recursion CVE.

### Impact Explanation
Stack exhaustion in the fault-proof program during derivation of L1 receipts/transactions (used for deposit and system-config derivation) or during L2 state-trie account/storage lookups causes the client program to crash (SIGSEGV / abort) rather than producing a valid execution trace or output root. This halts the fault-proof/dispute pipeline for the affected block, preventing the node from producing a provable output root for that derivation step — a Medium-severity denial-of-service against the derivation/proving pipeline, matching the qpdf analog's "denial of service via infinite recursion and stack consumption."

### Likelihood Explanation
A source of untrusted, attacker-influenced trie-node preimages is required (e.g., a malicious/compromised L1 RPC endpoint feeding the host program, or an adversarial party able to serve altered `debug_dbGet`/`debug_getRawReceipts` responses used to build the preimage KV store consumed by the client program). Given the rules' scope restriction on excluding malicious-node/malicious-peer/RPC-provider-only scenarios, the practically reachable trigger is more limited than a pure unprivileged-transaction path; I could not confirm within the available code whether preimage integrity (recursion-depth or size limits) is otherwise enforced at ingestion time in the host (`crates/proof/host/src/handler.rs`), so likelihood is uncertain without further investigation of the full preimage-validation pipeline.

### Recommendation
Add an explicit maximum recursion-depth parameter (mirroring `MAX_CBOR_NESTING_DEPTH` in `crates/proof/tee/registrar/src/cbor.rs`) threaded through `TrieNode::decode`, `try_decode_leaf_or_extension_payload`, `TrieNode::open`/`unblind`, and `OrderedListWalker::fetch_leaves`, rejecting any trie preimage chain exceeding the maximum possible real MPT depth (64 nibbles for 32-byte keys, plus a small safety margin), and/or convert these recursive traversals to explicit iterative stack-based algorithms.

### Proof of Concept
Not independently constructed/verified against a running host+client fault-proof pipeline; a concrete PoC would require crafting a chain of N nested RLP-encoded `Extension`/`Branch` `TrieNode` blobs (each referencing the next via a blinded 32-byte commitment) served as preimages through a controlled `TrieProvider`/`debug_dbGet` mock, then invoking `TrieNode::decode` or `OrderedListWalker::hydrate` on the root and observing stack overflow at depth N (e.g., N in the tens of thousands, well beyond the practical 64-nibble depth of a genuine Ethereum MPT).

### Citations

**File:** crates/proof/mpt/src/node.rs (L124-182)
```rust
    /// Unblinds the [`TrieNode`] if it is a [`TrieNode::Blinded`] node.
    pub fn unblind<F: TrieProvider>(&mut self, fetcher: &F) -> TrieNodeResult<()> {
        if let Self::Blinded { commitment } = self {
            if *commitment == EMPTY_ROOT_HASH {
                // If the commitment is the empty root hash, the node is empty, and we don't need to
                // reach out to the fetcher.
                *self = Self::Empty;
            } else {
                *self = fetcher
                    .trie_node_by_hash(*commitment)
                    .map_err(|e| TrieNodeError::Provider(e.to_string()))?;
            }
        }
        Ok(())
    }

    /// Walks down the trie to a leaf value with the given key, if it exists. Preimages for blinded
    /// nodes along the path are fetched using the `fetcher` function, and persisted in the inner
    /// [`TrieNode`] elements.
    ///
    /// ## Takes
    /// - `self` - The root trie node
    /// - `path` - The nibbles representation of the path to the leaf node
    /// - `fetcher` - The preimage fetcher for intermediate blinded nodes
    ///
    /// ## Returns
    /// - `Err(_)` - Could not retrieve the node with the given key from the trie.
    /// - `Ok(None)` - The node with the given key does not exist in the trie.
    /// - `Ok(Some(_))` - The value of the node
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

**File:** crates/proof/proof/src/l1/chain_provider.rs (L68-89)
```rust
    async fn receipts_by_hash(&mut self, hash: B256) -> Result<Vec<Receipt>, Self::Error> {
        // Fetch the block header to find the receipts root.
        let header = self.header_by_hash(hash).await?;

        // Send a hint for the block's receipts, and walk through the receipts trie in the header to
        // verify them.
        HintType::L1Receipts.with_data(&[hash.as_ref()]).send(self.oracle.as_ref()).await?;
        let trie_walker = OrderedListWalker::try_new_hydrated(header.receipts_root, self)
            .map_err(OracleProviderError::TrieWalker)?;

        // Decode the receipts within the receipts trie.
        let receipts = trie_walker
            .into_iter()
            .map(|(_, rlp)| {
                let envelope = ReceiptEnvelope::decode_2718(&mut rlp.as_ref())?;
                Ok(envelope.as_receipt().expect("Infallible").clone())
            })
            .collect::<Result<Vec<_>, _>>()
            .map_err(OracleProviderError::Rlp)?;

        Ok(receipts)
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

**File:** crates/proof/host/src/handler.rs (L986-996)
```rust
        HintType::L1Receipts => {
            if hint.data.len() != 32 {
                return Err(HostError::InvalidHintDataLength);
            }

            let hash: B256 = hint.data.as_ref().try_into()?;
            let raw_receipts: Vec<Bytes> =
                providers.l1.client().request("debug_getRawReceipts", [hash]).await?;

            store_ordered_trie(kv.as_ref(), raw_receipts.as_slice()).await?;
        }
```
