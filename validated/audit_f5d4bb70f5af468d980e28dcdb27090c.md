### Title
Unbounded Recursion in `OrderedListWalker::fetch_leaves` Causes Stack Overflow During Fault-Proof MPT Derivation - (File: `crates/proof/mpt/src/list_walker.rs`)

### Summary
`OrderedListWalker::fetch_leaves` recursively descends into `TrieNode::Branch` and `TrieNode::Extension` nodes with no depth bound, mirroring the exact bug class in CVE-2024-29131 (`AbstractListDelimiterHandler.flattenIterator()` unbounded recursion → `StackOverflowError`). This routine is used by the fault-proof program to derive the L1 receipts trie, L1 transactions trie, and L2 transactions trie from oracle-supplied preimages.

### Finding Description
`fetch_leaves` recurses once per `Branch` child and once per `Extension` node, calling itself again each time a `Blinded` commitment is resolved through the trie provider: [1](#0-0) 

There is no maximum-depth check, no iteration limit, and no conversion to an explicit work-stack — each nested `Extension`/`Branch` layer of the reconstructed MPT adds a native stack frame. The `TrieNode` RLP decoder that constructs these nodes from oracle preimages also has no depth restriction: [2](#0-1) 

This walker is invoked directly by the fault-proof program's oracle-backed chain providers to reconstruct L1 receipts, L1 transactions, and L2 transactions from a trie root supplied in an L1/L2 block header: [3](#0-2) [4](#0-3) [5](#0-4) 

Because every preimage node in this chain is supplied by the untrusted preimage oracle (attacker-controllable in the fault-dispute-game context, since the oracle is populated from data the disputing party provides/hints), a malicious dispute participant can craft a deeply nested (long chain of single-child `Branch`/`Extension`) MPT preimage set for the transactions or receipts root referenced by a disputed L1/L2 header. When the fault-proof program (the on-chain-verifiable client run inside the FPVM, e.g. in `crates/proof/client`) walks that trie via `OrderedListWalker::hydrate` → `fetch_leaves`, the recursive descent can exhaust the call stack.

### Impact Explanation
A stack overflow inside the fault-proof program during dispute-game execution causes the program to crash/trap instead of producing a deterministic output root. Since the fault-proof program's correctness underlies the dispute-game's ability to compute/verify the claimed output root, an attacker who can supply or influence the preimage data backing a disputed block's transactions/receipts trie can force the honest challenger's (or defender's) proof execution to fail, preventing production of a correct provable output root and potentially disrupting dispute-game resolution — a "wrong provable output root / node halt" class impact as scoped by the validation rules.

### Likelihood Explanation
Exploitability requires the attacker to control or influence the L1/L2 block's transactions/receipts trie preimages supplied through the preimage oracle during a fault-dispute game — a path reachable by any dispute-game participant crafting the disputed block's transaction/receipt set (e.g., submitting a very large number of L1/L2 transactions or receipts to force many `Branch` nodes and deep nesting), which is a lower-cost, protocol-level action rather than a memory-corruption exploit, making it a realistic Medium-likelihood DoS vector against the proof-generation path.

### Recommendation
Convert `OrderedListWalker::fetch_leaves` from recursive descent to an explicit iterative work-queue (e.g., using the existing `VecDeque`/stack-based traversal already used for `Branch::stack`) and/or impose an explicit maximum trie depth (MPT depth is bounded in practice by 64 nibbles/2 hex-per-byte for 32-byte keys, so a hard cap is safe), rejecting nodes beyond that depth with `TrieNodeError::InvalidNodeType` rather than recursing further.

### Proof of Concept
1. In a fault-dispute game, submit/derive an L1 or L2 block whose transactions or receipts trie is deliberately shaped by the attacker (e.g., by crafting many similarly-prefixed transaction hashes or by controlling the preimage set returned to the oracle) so that `fetch_leaves`'s recursive branch/extension traversal reaches thousands of nested calls.
2. Trigger the fault-proof program to derive that block via `OracleL1ChainProvider::receipts_by_hash`/`block_info_and_transactions_by_hash` or `OracleL2ChainProvider::block_by_number`, both of which call `OrderedListWalker::try_new_hydrated` → `fetch_leaves`.
3. Observe the FPVM/native proof-client process crash with a stack overflow instead of producing an output root, matching the `CVE-2024-29131` bug class of unbounded-recursion-triggered stack exhaustion on attacker-shaped nested input.

### Citations

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

**File:** crates/proof/mpt/src/node.rs (L567-606)
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

**File:** crates/proof/proof/src/l1/chain_provider.rs (L91-123)
```rust
    async fn block_info_and_transactions_by_hash(
        &mut self,
        hash: B256,
    ) -> Result<(BlockInfo, Vec<TxEnvelope>), Self::Error> {
        // Fetch the block header to construct the block info.
        let header = self.header_by_hash(hash).await?;
        let block_info = BlockInfo {
            hash,
            number: header.number,
            parent_hash: header.parent_hash,
            timestamp: header.timestamp,
        };

        // Send a hint for the block's transactions, and walk through the transactions trie in the
        // header to verify them.
        HintType::L1Transactions.with_data(&[hash.as_ref()]).send(self.oracle.as_ref()).await?;
        let trie_walker = OrderedListWalker::try_new_hydrated(header.transactions_root, self)
            .map_err(OracleProviderError::TrieWalker)?;

        // Decode the transactions within the transactions trie.
        let transactions = trie_walker
            .into_iter()
            .map(|(_, rlp)| {
                // note: not short-handed for error type coercion w/ `?`.
                let rlp = TxEnvelope::decode_2718(&mut rlp.as_ref())?;
                Ok(rlp)
            })
            .collect::<Result<Vec<_>, _>>()
            .map_err(OracleProviderError::Rlp)?;

        Ok((block_info, transactions))
    }
}
```

**File:** crates/proof/proof/src/l2/chain_provider.rs (L115-152)
```rust
    async fn block_by_number(&mut self, number: u64) -> Result<BaseBlock, Self::Error> {
        // Fetch the header for the given block number.
        let header @ Header { transactions_root, timestamp, .. } =
            self.header_by_number(number).await?;
        let header_hash = header.hash_slow();

        // Fetch the transactions in the block.
        HintType::L2Transactions
            .with_data(&[header_hash.as_ref()])
            .with_data(self.chain_id.map_or_else(Vec::new, |id| id.to_be_bytes().to_vec()))
            .send(self.oracle.as_ref())
            .await?;
        let trie_walker = OrderedListWalker::try_new_hydrated(transactions_root, self)
            .map_err(OracleProviderError::TrieWalker)?;

        // Decode the transactions within the transactions trie.
        let transactions = trie_walker
            .into_iter()
            .map(|(_, rlp)| {
                let res = BaseTxEnvelope::decode_2718_exact(rlp.as_ref())?;
                Ok(res)
            })
            .collect::<Result<Vec<_>, _>>()
            .map_err(OracleProviderError::Rlp)?;

        let block = BaseBlock {
            header,
            body: BlockBody {
                transactions,
                ommers: Vec::new(),
                withdrawals: self
                    .rollup_config
                    .is_canyon_active(timestamp)
                    .then(|| alloy_eips::eip4895::Withdrawals::new(Vec::new())),
            },
        };
        Ok(block)
    }
```
