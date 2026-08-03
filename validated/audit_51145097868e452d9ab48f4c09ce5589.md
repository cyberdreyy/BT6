[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** storage/storage-interface/src/state_store/sharded_jmt_state.rs (L80-101)
```rust
    pub fn extend(&self, new_version: Version, updates: Vec<(HashValue, Slot)>) -> Self {
        let mut per_shard: [Vec<(HashValue, Slot)>; NUM_STATE_SHARDS] = arr![Vec::new(); 16];
        for (key_hash, slot) in updates {
            per_shard[usize::from(key_hash.nibble(0))].push((key_hash, slot));
        }
        let new_shards: Vec<MapLayer<HashValue, Slot>> = self
            .shards
            .iter()
            .enumerate()
            .map(|(shard_id, base_layer)| {
                let view = base_layer.view_layers_after(base_layer);
                view.new_layer(&per_shard[shard_id])
            })
            .collect();
        let new_shards: [MapLayer<HashValue, Slot>; NUM_STATE_SHARDS] = new_shards
            .try_into()
            .unwrap_or_else(|_| panic!("Known to be 16 shards"));
        Self {
            next_version: new_version + 1,
            shards: Arc::new(new_shards),
        }
    }
```

**File:** storage/storage-interface/src/state_store/sharded_jmt_state.rs (L177-185)
```rust
pub type PositionSlot = LeafSlot<()>;

pub type PositionStateSummary = StateSummary;

pub type PositionStateWithSummary = StateAndSummary<ShardedJmtState<PositionSlot>>;

pub fn new_empty_position_state() -> PositionStateWithSummary {
    PositionStateWithSummary::new_empty("position")
}
```

**File:** storage/storage-interface/src/state_store/state_summary.rs (L1-14)
```rust
// Copyright (c) Aptos Foundation
// Licensed pursuant to the Innovation-Enabling Source Code License, available at https://github.com/aptos-labs/aptos-core/blob/main/LICENSE

use crate::{
    metrics::TIMER,
    state_store::{
        sharded_jmt_state::PositionStateWithSummary,
        state::LedgerState,
        state_update_refs::{BatchedStateUpdateRefs, StateUpdateRefs},
        state_with_summary::LedgerWithSummary,
        HotStateShardUpdates, HotStateUpdates,
    },
    DbReader,
};
```

**File:** storage/aptosdb/src/db/aptosdb_native_position.rs (L255-277)
```rust
        let mut pending_leaf_updates: HashMap<HashValue, PositionSlot> = HashMap::new();
        for write_set in &write_sets {
            for (key, op) in write_set.native_position_iter() {
                let maybe_value = op.as_write_op().as_state_value_opt().cloned();
                let value_hash = maybe_value.as_ref().map(StateValue::hash);
                pending_leaf_updates.insert(key.hash(), PositionSlot {
                    state_key: key.clone(),
                    value_hash,
                    value: None,
                });
            }
        }

        if pending_leaf_updates.is_empty() {
            return Ok(());
        }

        let state_lock = store.current_state();
        let pipeline_latest = state_lock.lock().latest().clone();
        let snapshot_version = pipeline_latest.version();

        let target_version = num_transactions - 1;
        let updates: Vec<_> = pending_leaf_updates.into_iter().collect();
```
