No vulnerability found for this question.

**Reasoning:** Gas metering for trie node access is charged at the storage-retrieval layer (`move_node_to_mutable` / `internal_retrieve_trie_node`), not inside `squash.rs`. When `ensure_updated` triggers a disk load, that load already goes through `Trie`'s read path, which tracks `db_reads`/`mem_reads` via `AccountingAccessTracker`/`AccountingState`, later charged through `commit_counts_since` calling `trie_node_touched`/`cached_trie_node_access` on the `GasCounter` [1](#0-0) . The `GasCounter`'s `StorageAccessTracker` impl performs the actual `pay_per(ExtCosts::touching_trie_node, ...)` charge [2](#0-1) .

The `extend_child` function itself only manipulates already-fetched in-memory node data — `self.ensure_updated(child_id, opts)?` at squash.rs:120 delegates to `TrieStorageUpdate::ensure_updated`, which calls `self.trie.move_node_to_mutable(...)`, the actual read/charge path [3](#0-2) . The subsequent `child_child_memory_usage` subtraction at squash.rs:122 is pure in-memory bookkeeping of the node's `memory_usage` field (used for trie structural invariants/hashing), unrelated to gas accounting [4](#0-3) .

The premise that "no GasCounter call between lines 120–122" indicates a fee bypass is incorrect: the design intentionally separates node-retrieval gas accounting (done deep inside the storage/trie layer before `ensure_updated` returns) from higher-level trie restructuring logic (`squash_node`/`extend_child`), which never needs to call `GasCounter` directly because the expensive disk read was already charged by the time the node materializes. A grep-based check for `pay_per`/`trie_node_touched` calls in `squash.rs` finding none is expected and does not indicate an uncharged operation — it reflects correct separation of concerns, not a bypass. No concrete attacker-triggerable fee-bypass, fund theft, or consensus-divergence path exists here.

### Citations

**File:** runtime/runtime/src/ext.rs (L699-717)
```rust
    fn commit_counts_since(
        &self,
        snapshot: TrieNodesCount,
        into: &mut dyn StorageAccessTracker,
    ) -> Result<TrieNodesCount, VMLogicError> {
        let db_read_delta = self
            .db_reads
            .load(Ordering::Relaxed)
            .checked_sub(snapshot.db_reads)
            .ok_or(InconsistentStateError::IntegerOverflow)?;
        let mem_read_delta = self
            .mem_reads
            .load(Ordering::Relaxed)
            .checked_sub(snapshot.mem_reads)
            .ok_or(InconsistentStateError::IntegerOverflow)?;
        into.trie_node_touched(db_read_delta)?;
        into.cached_trie_node_access(mem_read_delta)?;
        Ok(TrieNodesCount { db_reads: db_read_delta, mem_reads: mem_read_delta })
    }
```

**File:** runtime/near-vm-runner/src/logic/gas_counter.rs (L398-413)
```rust
impl StorageAccessTrackerSeal for GasCounter {}
impl StorageAccessTracker for GasCounter {
    fn trie_node_touched(&mut self, count: u64) -> Result<()> {
        self.pay_per(ExtCosts::touching_trie_node, count)
    }

    fn cached_trie_node_access(&mut self, count: u64) -> Result<()> {
        self.pay_per(ExtCosts::read_cached_trie_node, count)
    }

    fn deref_write_evicted_value_bytes(&mut self, bytes: u64) -> Result<()> {
        self.pay_per(ExtCosts::storage_write_evicted_byte, bytes)
    }
    fn deref_removed_value_bytes(&mut self, bytes: u64) -> Result<()> {
        self.pay_per(ExtCosts::storage_remove_ret_value_byte, bytes)
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

**File:** core/store/src/trie/ops/squash.rs (L120-122)
```rust
        let child_id = self.ensure_updated(child_id, opts)?;
        let GenericUpdatedTrieNodeWithSize { node, memory_usage } = self.take_node(child_id);
        let child_child_memory_usage = memory_usage.saturating_sub(node.memory_usage_direct());
```
