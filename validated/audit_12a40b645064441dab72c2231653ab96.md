No vulnerability found for this question.

**Analysis:**

The accounting cache mechanism is explicitly designed to handle exactly this repeated-access pattern, and it works as intended rather than creating a fee bypass:

1. `internal_retrieve_trie_node` at [1](#0-0)  checks `access_options.trie_access_tracker.track_mem_lookup(hash)` first; only on a miss does it call `retrieve_raw_bytes` and then `track_disk_lookup`. This is invoked by `retrieve_raw_node` → `move_node_to_mutable`, always with `use_accounting_cache=true` [2](#0-1) , which is what `TrieStorageUpdate::ensure_updated` calls for `GenericNodeOrIndex::Old` nodes [3](#0-2) .

2. When `squash_node`'s `Extension` arm calls `extend_child`, which calls `ensure_updated(child_id, opts)` on the child at squash.rs:120 [4](#0-3) , if that same child hash was already resolved during the initial `generic_delete` descent, the second lookup hits `track_mem_lookup` and is charged at the *cheaper* `read_cached_trie_node` gas cost, not the full `touching_trie_node` cost, and not for free.

3. This exact behavior — full-price charge on first access, reduced-price charge on cache hit within the same chunk — is the documented, intended purpose of the accounting cache/`ChunkNodesCache` feature, confirmed by the test comments and assertions in `chunk_nodes_cache.rs` [5](#0-4)  and `test_accounting_cache_common_parent` [6](#0-5) , and by the doc comment on `AccountingAccessTracker` explaining that the reduced fee for repeated in-cache access is intentional and its cost accounted for in the runtime fee schedule [7](#0-6) .

There is no double full-price charge, and the second (cheaper) charge is a real, non-zero gas cost matching the actual reduced work of a cache hit — this is by design, not a bypass or unintended subsidy. No refund mechanism is needed because no over-charge occurs. This does not constitute a reachable fund-loss, freezing, or consensus-divergence bug under an unprivileged attacker model.

### Citations

**File:** core/store/src/trie/mod.rs (L778-795)
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
```

**File:** core/store/src/trie/mod.rs (L1199-1213)
```rust
    pub(crate) fn move_node_to_mutable(
        &self,
        trie_update: &mut TrieStorageUpdate,
        hash: &CryptoHash,
        opts: AccessOptions,
    ) -> Result<StorageHandle, StorageError> {
        match self.retrieve_raw_node(hash, true, opts)? {
            None => Ok(trie_update.store(UpdatedTrieStorageNodeWithSize::empty())),
            Some((_, node)) => {
                let result = trie_update
                    .store(TrieStorageNodeWithSize::from_raw_trie_node_with_size(node).into());
                trie_update.refcount_changes.subtract(*hash, 1);
                Ok(result)
            }
        }
```

**File:** core/store/src/trie/trie_storage_update.rs (L92-103)
```rust
    fn ensure_updated(
        &mut self,
        node: GenericNodeOrIndex<TrieStorageNodePtr>,
        opts: AccessOptions,
    ) -> Result<UpdatedNodeId, StorageError> {
        match node {
            GenericNodeOrIndex::Old(node_hash) => {
                self.trie.move_node_to_mutable(self, &node_hash, opts).map(|handle| handle.0)
            }
            GenericNodeOrIndex::Updated(node_id) => Ok(node_id),
        }
    }
```

**File:** core/store/src/trie/ops/squash.rs (L119-122)
```rust
    ) -> Result<(), StorageError> {
        let child_id = self.ensure_updated(child_id, opts)?;
        let GenericUpdatedTrieNodeWithSize { node, memory_usage } = self.take_node(child_id);
        let child_child_memory_usage = memory_usage.saturating_sub(node.memory_usage_direct());
```

**File:** integration-tests/src/tests/features/chunk_nodes_cache.rs (L66-84)
```rust
/// NOTE: The comment below is no longer valid as we are now only checking for the latest protocol version.
///
/// Compare charged node accesses before and after protocol upgrade to the protocol version of `ChunkNodesCache`.
/// This upgrade during chunk processing saves each node for which we charge touching trie node cost to a special
/// accounting cache (used to be called "chunk cache"), and such cost is charged only once on the first access.
/// This effect doesn't persist across chunks.
///
/// We run the same transaction 4 times and compare resulting costs. This transaction writes two different key-value
/// pairs to the contract storage.
/// 1st run establishes the trie structure. For our needs, the structure is:
///
///                                                    --> (Leaf) -> (Value 1)
/// (Extension) -> (Branch) -> (Extension) -> (Branch) |
///                                                    --> (Leaf) -> (Value 2)
///
/// 2nd run should count 12 regular db reads - for 6 nodes per each value, because protocol is not upgraded yet.
/// 3nd run follows the upgraded protocol and it should count 8 db and 4 memory reads, which comes from 6 db reads
/// for `Value 1` and only 2 db reads for `Value 2`, because first 4 nodes were already put into the accounting
/// cache. 4nd run should give the same results, because caching must not affect different chunks.
```

**File:** integration-tests/src/tests/standard_cases/mod.rs (L1906-1935)
```rust
/// Check correctness of charging for trie node accesses with enabled chunk nodes cache.
/// We run the same set of receipts 2 times and compare resulting trie node counts. Each receipt writes some key-value
/// pair to the contract storage.
/// 1st run establishes the trie structure. For our needs, the structure is:
///
///                                                    --> (Leaf) -> (Value 1)
/// (Extension) -> (Branch) -> (Extension) -> (Branch) |-> (Leaf) -> (Value 2)
///                                                    --> (Leaf) -> (Value 3)
///
/// 1st receipt should count 6 db reads.
/// 2nd and 3rd receipts should count 2 db and 4 memory reads, because for them first 4 nodes were already put into the
/// accounting cache.
pub fn test_accounting_cache_common_parent(node: impl Node, runtime_config: RuntimeConfig) {
    let receipts: Vec<Receipt> = (0..3)
        .map(|i| {
            make_receipt(
                &node,
                vec![make_write_key_value_action(vec![i], vec![10u64 + i])],
                bob_account(),
            )
        })
        .collect();

    let results = vec![
        TrieNodesCount { db_reads: 6, mem_reads: 0 },
        TrieNodesCount { db_reads: 2, mem_reads: 4 },
        TrieNodesCount { db_reads: 2, mem_reads: 4 },
    ];
    check_trie_nodes_count(&node, &runtime_config, receipts, results, true);
}
```

**File:** runtime/runtime/src/ext.rs (L646-678)
```rust
/// Deterministic cache to store trie nodes that have been accessed so far
/// during the cache's lifetime. It is used for deterministic gas accounting
/// so that previously accessed trie nodes and values are charged at a
/// cheaper gas cost.
///
/// This cache's correctness is critical as it contributes to the gas accounting of storage
/// operations during contract execution. For that reason, a new `AccountingState` must be
/// created at the beginning of a chunk's execution, and the db_read_nodes and mem_read_nodes must
/// be taken into account whenever a contract storage operation is performed to calculate what kind
/// of operation it was.
///
/// The latter is easy as the only way a contract storage operation can happen is through the
/// implementation of `Externals`.
///
/// Note that we don't have a size limit for values in the accounting cache.
/// There are two reasons:
///   - for nodes, value size is an implementation detail. If we change
///     internal representation of a node (e.g. change `memory_usage` field
///     from `RawTrieNodeWithSize`), this would have to be a protocol upgrade.
///   - total size of all values is limited by the runtime fees. More
///     thoroughly:
///       - number of nodes is limited by receipt gas limit / touching trie
///         node fee ~= 500 Tgas / 16 Ggas = 31_250;
///       - size of trie keys and values is limited by receipt gas limit /
///         lowest per byte fee (`storage_read_value_byte`) ~=
///         (500 * 10**12 / 5611005) / 2**20 ~= 85 MB.
/// All values are given as of 16/03/2022. We may consider more precise limit
/// for the accounting cache as well.
///
/// Note that in general, it is NOT true that all storage access is either a db read or mem read.
/// It can also be a flat storage read, which is not tracked via `AccountingAccessTracker`, except
/// for value dereferences that ultimately go out to trie anyway.
// FIXME(nagisa): equalize fees for different types of accesses and eventually remove this code.
```
