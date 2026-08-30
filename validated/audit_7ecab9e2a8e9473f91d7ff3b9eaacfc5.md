This confirms the vulnerability claim is invalid. The critical fact: `prepare_transactions_extra` (which uses `TrieUpdate::clone_for_tx_preparation` via `TrieUpdateWitnessSizeWrapper`) is a **producer-side-only, non-consensus-critical selection step**. Its recorded storage proof is used solely to decide *which transactions to include* in the chunk (`PrepareTransactionsLimit::StorageProofSize`), and is never included in the actual `ChunkStateWitness`.

The state witness's `main_state_transition().base_state` — the actual `PartialState` proof that validators replay against — comes from a **separate, independent execution**: `apply_new_chunk` → `Runtime::apply`, which builds its own fresh `TrieUpdate`/recorder from the actual chunk (already-decided transaction list + receipts) against the real prev-chunk state root, as shown in `chain/chain/src/stateless_validation/chunk_validation.rs:406-444` (`MainTransition::NewChunk` built from `StorageDataSource::Recorded(PartialStorage { nodes: state_witness.main_state_transition().base_state... })`) and `chunk_validation.rs:568-624` (`apply_new_chunk` replay).

Because the tx-preparation recorder's proof is discarded and never becomes part of the witness, any node reading from `prospective`/`committed` (`get_ref_from_updates`, `core/store/src/trie/update.rs:136-144`) that bypasses the *tx-prep* recorder cannot cause the *actual* witness (built during real chunk application, which does not share `prospective`/`committed` with the tx-prep clone at all) to omit needed trie nodes. Both the chunk producer's real application and the chunk validator's replay start from the same finalized state root and execute the same included transactions/receipts through the same code path (`Runtime::apply`), so their recorded proofs are consistent by construction — there is no divergence introduced by `clone_for_tx_preparation`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

#No vulnerability found for this question.

### Citations

**File:** core/store/src/trie/update.rs (L90-100)
```rust
    /// Clones the `TrieUpdate` for transaction preparation.
    /// The cloned `TrieUpdate` will have a new recorder for trie reads,
    /// while sharing the same underlying committed and prospective changes.
    pub fn clone_for_tx_preparation(&self) -> TrieUpdate {
        Self {
            trie: self.trie.recording_reads_new_recorder(),
            contract_storage: ContractStorage::new(self.trie.storage.clone()),
            committed: self.committed.clone(),
            prospective: self.prospective.clone(),
        }
    }
```

**File:** chain/chain/src/runtime/mod.rs (L925-928)
```rust
        // Interim updates for accounts and nonces are written to signer_overlay,
        // not back to the state_update.
        let state_update = TrieUpdateWitnessSizeWrapper::new(storage);
        let mut signer_overlay = SignerOverlay::new();
```

**File:** chain/chain/src/stateless_validation/chunk_validation.rs (L416-440)
```rust
    } else {
        let transactions = SignedValidPeriodTransactions::new(
            state_witness.transactions().clone(),
            transaction_validity_check_results,
        );
        let header = store.get_block_header(last_chunk_block.header().prev_hash())?;

        let last_chunk_block_chunks = last_chunk_block.chunks();
        let chunk_header = last_chunk_block_chunks.get(last_chunk_shard_index).unwrap();
        MainTransition::NewChunk {
            new_chunk_data: NewChunkData {
                gas_limit: chunk_header.gas_limit(),
                prev_state_root: chunk_header.prev_state_root(),
                prev_validator_proposals: chunk_header.prev_validator_proposals().collect(),
                chunk_hash: Some(chunk_header.chunk_hash().clone()),
                transactions,
                receipts: receipts_to_apply,
                block: Chain::get_apply_chunk_block_context(last_chunk_block, &header, true),
                storage_context: StorageContext {
                    storage_data_source: StorageDataSource::Recorded(PartialStorage {
                        nodes: state_witness.main_state_transition().base_state.clone(),
                    }),
                    state_patch: Default::default(),
                },
            },
```

**File:** chain/chain/src/stateless_validation/chunk_validation.rs (L603-624)
```rust
    let (mut chunk_extra, mut outgoing_receipts) =
        match (pre_validation_output.main_transition_params, cache_result) {
            (MainTransition::Genesis { chunk_extra, .. }, _) => (chunk_extra, vec![]),
            (MainTransition::NewChunk { new_chunk_data, .. }, None) => {
                let chunk_gas_limit = new_chunk_data.gas_limit;
                let NewChunkResult { apply_result: mut main_apply_result, .. } = apply_new_chunk(
                    ApplyChunkReason::ValidateChunkStateWitness,
                    &span,
                    new_chunk_data,
                    ShardContext { shard_uid, should_apply_chunk: true },
                    runtime_adapter,
                    // Recorded-storage replay; no memtrie path.
                    MaybePinnedMemtrieRoot::no_memtries(),
                    None,
                )?;
                let outgoing_receipts = std::mem::take(&mut main_apply_result.outgoing_receipts);
                let chunk_extra = main_apply_result.to_chunk_extra(chunk_gas_limit);

                (chunk_extra, outgoing_receipts)
            }
            (_, Some(result)) => (result.chunk_extra, result.outgoing_receipts),
        };
```
