Based on my analysis of the code, this finding does not hold up.

The critical detail is in `internal_retrieve_trie_node` (`core/store/src/trie/mod.rs:778-802`): recording into the `TrieRecorder` (governed by `access_options.enable_state_witness_recording`) happens **unconditionally after** materializing the node's bytes, regardless of whether those bytes came from a `track_mem_lookup` cache hit or a fresh `retrieve_raw_bytes` disk read gated by `use_trie_accounting_cache`. [1](#0-0) 

That means the accounting-cache flag (`use_trie_accounting_cache`, forced `true` for `KeyLookupMode::MemOrTrie` at `mod.rs:1487`) only controls whether the gas-tracking `TrieAccountingCache`/`AccountingState` sees the access as a cheap "mem read" vs. a "db read" — it does **not** gate whether the underlying node bytes get pushed into the recorder/proof. Recording is tied to `access_options.enable_state_witness_recording`, a separate, orthogonal flag. [2](#0-1) 

Furthermore, in the flat-storage branch of `contains_key_mode` specifically, when a recorder is attached the code deliberately performs a *shadow* trie walk (`lookup_from_state_column(key, false, opts)`) purely to force those exact proving nodes into the recorder even though the flat-storage answer was already known — this is a defense-in-depth mechanism precisely to keep the proof self-contained regardless of which path answered the query. [3](#0-2) 

So whichever call happens first (via `MemOrTrie`, forcing `use_trie_accounting_cache = true`) will populate the accounting cache with a node's bytes only after that node has already been recorded via `internal_retrieve_trie_node`/`lookup_from_memory`. A subsequent `MemOrFlatOrTrie` call that later hits the same accounting-cache entry (fast, cheap "mem read" gas charge) cannot make the recorded proof incomplete, because the node was already recorded at insertion time into the cache, and the flat-path's own shadow walk records it again independently. There is no code path where a value can enter the accounting cache without the corresponding node having gone through the recorder when `enable_state_witness_recording` is set (which is the mode used during chunk production / state-witness generation, the only place `METERING_TOTALITY` matters for consensus).

This is also exercised by existing tests such as `test_accounting_cache_mode` and `test_trie_recording_consistency`, which specifically assert that accounting-cache-driven gas counts stay consistent with recorded partial storage across mixed lookup modes. [4](#0-3) [5](#0-4) 

#No vulnerability found for this question.

### Citations

**File:** core/store/src/trie/mod.rs (L778-801)
```rust
    fn internal_retrieve_trie_node(
        &self,
        hash: &CryptoHash,
        use_accounting_cache: bool,
        access_options: AccessOptions,
    ) -> Result<Arc<[u8]>, StorageError> {
        let result = if use_accounting_cache {
            match access_options.trie_access_tracker.track_mem_lookup(hash) {
                Some(v) => v,
                None => {
                    let v = self.storage.retrieve_raw_bytes(hash)?;
                    access_options.trie_access_tracker.track_disk_lookup(*hash, Arc::clone(&v));
                    v
                }
            }
        } else {
            self.storage.retrieve_raw_bytes(hash)?
        };
        if access_options.enable_state_witness_recording {
            if let Some(recorder) = &self.recorder {
                recorder.record(hash, result.clone());
            }
        }
        Ok(result)
```

**File:** core/store/src/trie/mod.rs (L1369-1380)
```rust
                if use_trie_accounting_cache {
                    if access_options.trie_access_tracker.track_mem_lookup(&node_hash).is_none() {
                        access_options
                            .trie_access_tracker
                            .track_disk_lookup(node_hash, get_serialized_node());
                    }
                }
                if access_options.enable_state_witness_recording {
                    if let Some(recorder) = &self.recorder {
                        recorder.record_with(&node_hash, get_serialized_node);
                    }
                }
```

**File:** core/store/src/flat/mod.rs (L1498-1506)
```rust

```

**File:** integration-tests/src/tests/standard_cases/mod.rs (L1974-1994)
```rust
/// We have checked manually that if accounting cache mode is not disabled, then the following scenario happens:
/// - 1st receipt enables accounting cache mode but doesn't disable it
/// - 2nd receipt triggers insertion of `Value 2` into the accounting cache
/// - 3rd receipt reads it from the accounting cache, so it incorrectly charges user for 1 db and 5 memory reads.
pub fn test_accounting_cache_mode(node: impl Node, runtime_config: RuntimeConfig) {
    let receipts: Vec<Receipt> = vec![
        make_receipt(&node, vec![make_write_key_value_action(vec![1], vec![1])], bob_account()),
        make_receipt(
            &node,
            vec![DeployContractAction { code: test_utils::encode(&[2]) }.into()],
            alice_account(),
        ),
        make_receipt(&node, vec![make_write_key_value_action(vec![2], vec![2])], bob_account()),
    ];

    let results = vec![
        TrieNodesCount { db_reads: 6, mem_reads: 0 },
        TrieNodesCount { db_reads: 0, mem_reads: 0 },
        TrieNodesCount { db_reads: 2, mem_reads: 4 },
    ];
    check_trie_nodes_count(&node, &runtime_config, receipts, results, true);
```

**File:** core/store/src/trie/trie_recording.rs (L609-612)
```rust
    /// Verifies that when operating on a trie, the results are completely consistent
    /// regardless of whether we're operating on the real storage (with or without chunk
    /// cache), while recording reads, or when operating on recorded partial storage.
    fn test_trie_recording_consistency(
```
