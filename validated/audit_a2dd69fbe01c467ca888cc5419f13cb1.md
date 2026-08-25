### Title
`EntryBytesBudget` reservation is never released when block recording fails, permanently wasting per-slot entry-byte capacity - (File: `core/src/banking_stage/consumer.rs`)

### Summary
`EntryBytesBudget::reserve()` in `runtime/src/bank/entry_bytes_budget.rs` only supports incrementing the "consumed" counter; there is no corresponding release/refund method. In `Consumer::execute_and_commit_transactions_locked` (`core/src/banking_stage/consumer.rs`), bytes are reserved from this budget just before calling `self.transaction_recorder.record_transactions(...)`. If recording fails, the code explicitly rolls back the symmetric `cost_tracker` reservation via `Self::remove_added_transaction_costs(bank, &transaction_costs)`, but it never releases the `entry_bytes_budget` reservation that was taken moments earlier for the same never-recorded batch.

### Finding Description
The relevant flow: [1](#0-0) 
reserves `entry_bytes` from the bank's per-slot `EntryBytesBudget` before recording, and: [2](#0-1) 
shows that on a recorder error the transaction *costs* are explicitly removed from `cost_tracker` (`Self::remove_added_transaction_costs`), but no equivalent call exists to give back the `entry_bytes` that were just reserved for the same failed, non-recorded batch.

`EntryBytesBudget` itself has no release/refund API at all: [3](#0-2) 
Only `reserve()` exists; `consumed` is a monotonically-increasing `AtomicU64` for the lifetime of the bank/slot (it's only reset when a new `EntryBytesBudget` is constructed on slot-time parameter changes, not per commit attempt): [4](#0-3) 

This is structurally the same bug class as the external report: a resource limit/budget is debited when an action is attempted, and a "symmetric restore" is correctly implemented for one accounting structure (`cost_tracker.remove()`, mirroring the DeFi protocol's `dailyDebtIncreaseLimitLeft += assets` on `repay()`), while an analogous accounting structure for the exact same failed action (`entry_bytes_budget`, mirroring `liquidate()`'s missing `dailyDebtIncreaseLimitLeft` credit) is never restored, even though the corresponding transactions were never actually recorded into the block.

Because `record_transactions` can fail for reasons unrelated to the byte payload itself (e.g. `PohRecorderError::MaxHeightReached` from the recorder, or other bank/PoH state races racing with the freeze lock), a leader can repeatedly attempt to record batches that ultimately fail to record, each time permanently consuming `entry_bytes` capacity from `EntryBytesBudget` for that slot without ever placing the corresponding bytes in the block.

### Impact Explanation
`EntryBytesBudget::reserve` gates how many serialized entry bytes may be placed into a slot (`max_entry_bytes_per_slot`, e.g. `20 * 1024 * 1024` in `LEGACY_SLOT_PARAMS`): [5](#0-4) 
If reservations leak on the recorder-failure path, the leader's own slot-time entry-byte capacity is silently consumed without any entries actually being produced. In the worst case this can exhaust the slot's entry-byte budget entirely, causing `reserve()` to start returning `EntryBytesReserveError::ExceedsSlotLimit` (mapped to `PohRecorderError::MaxHeightReached`) for all subsequent legitimate transaction batches in that slot, effectively truncating block production and reducing throughput/utilization for that leader slot — an ingest-starvation-style degradation of block production capacity, self-inflicted by the leader's own banking stage rather than by an external actor. This matches the "excess of assets available... utilization rate decrease" impact pattern of the original finding, translated to block-space capacity.

### Likelihood Explanation
Likelihood depends on how often `record_transactions` fails after the entry-byte reservation succeeds. The `bank_already_frozen` early-return path (lines 306–330) is handled *before* the reservation and does not leak. However, the reservation-then-record sequence at lines 371–381 can still fail via the transaction recorder returning an `Err` (captured at line 394 `Err(err) => (Err(err), None)`), which is handled at lines 397–414 without restoring `entry_bytes_budget`. This is a normal (non-adversarial) operational path in banking stage under contention/timing pressure near the end of a slot, so it is plausible in production leader operation, though I could not fully trace every caller of `record_transactions`/`PohRecorderError` to quantify frequency, since `solana_poh::transaction_recorder` internals were not indexed in this pass.

### Recommendation
Add a `release`/`unreserve` method to `EntryBytesBudget` that decrements `consumed` by a given amount (mirroring `CostTracker::remove`), and call it alongside `Self::remove_added_transaction_costs(bank, &transaction_costs)` in the recorder-error branch of `execute_and_commit_transactions_locked`, passing back the same `entry_bytes` value that was reserved for the batch that failed to record.

### Proof of Concept
Conceptual reproduction (not run, based on code reading):
1. Configure a bank near the boundary of `max_entry_bytes_per_slot`.
2. Repeatedly submit transaction batches through banking stage such that `entry_bytes_budget().reserve(entry_bytes)` succeeds but `self.transaction_recorder.record_transactions(...)` subsequently returns `Err` (e.g., by racing bank freeze/PoH height limits).
3. Observe that `bank.entry_bytes_budget()`'s internal `consumed` counter (only inspectable via `EntryBytesReserveError::ExceedsSlotLimit` from further `reserve` calls, since there is no public getter for `consumed`) keeps climbing even though no entries were recorded, while `bank.read_cost_tracker().unwrap().block_cost()`/`transaction_count()` correctly stay unaffected due to `remove_added_transaction_costs`.
4. Eventually, further legitimate transaction batches fail `reserve()` with `ExceedsSlotLimit` well before the slot has actually produced `max_entry_bytes_per_slot` bytes of entries.

I was not able to fully verify whether any other layer (e.g., PoH recorder itself) performs a compensating rollback of `entry_bytes_budget` outside `consumer.rs`, since `solana_poh::transaction_recorder` was not covered by the available index; this should be double-checked before treating the finding as fully confirmed.

### Citations

**File:** core/src/banking_stage/consumer.rs (L371-381)
```rust
        let reserved_bytes =
            bank.entry_bytes_budget()
                .reserve(entry_bytes)
                .map_err(|err| match err {
                    EntryBytesReserveError::ExceedsSlotLimit => PohRecorderError::MaxHeightReached,
                });
        let (record_transactions_summary, record_us) = measure_us!(reserved_bytes.map(|_| {
            self.transaction_recorder
                .record_transactions(bank.bank_id(), processed_transactions)
        }));
        execute_and_commit_timings.record_us = record_us;
```

**File:** core/src/banking_stage/consumer.rs (L397-414)
```rust
        if let Err(recorder_err) = recording_result {
            Self::remove_added_transaction_costs(bank, &transaction_costs);

            Self::extend_processed_retryable_transaction_indexes(
                &mut retryable_transaction_indexes,
                &processing_results,
            );

            return ExecuteAndCommitTransactionsOutput {
                cost_model_throttled_transactions_count,
                cost_model_us,
                transaction_counts,
                retryable_transaction_indexes,
                commit_transactions_result: Err(recorder_err),
                execute_and_commit_timings,
                error_counters,
            };
        }
```

**File:** runtime/src/bank/entry_bytes_budget.rs (L1-43)
```rust
use std::sync::atomic::{AtomicU64, Ordering};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EntryBytesReserveError {
    ExceedsSlotLimit,
}

#[derive(Debug)]
pub struct EntryBytesBudget {
    consumed: AtomicU64,
    slot_limit: u64,
}

impl EntryBytesBudget {
    pub const fn new(slot_limit: u64) -> Self {
        Self {
            consumed: AtomicU64::new(0),
            slot_limit,
        }
    }

    pub const fn slot_limit(&self) -> u64 {
        self.slot_limit
    }

    pub fn reserve(&self, bytes: u64) -> std::result::Result<(), EntryBytesReserveError> {
        loop {
            let current = self.consumed.load(Ordering::Acquire);
            let next = current.saturating_add(bytes);
            if next > self.slot_limit {
                return Err(EntryBytesReserveError::ExceedsSlotLimit);
            }

            if self
                .consumed
                .compare_exchange(current, next, Ordering::AcqRel, Ordering::Acquire)
                .is_ok()
            {
                return Ok(());
            }
        }
    }
}
```

**File:** runtime/src/bank.rs (L4934-4941)
```rust
    /// Applies slot-time changes for runtime-only fields. This function is
    /// expected to be idempotent.
    fn apply_slot_time_runtime_changes(&mut self) {
        self.entry_bytes_consumed =
            EntryBytesBudget::new(self.current_slot_params().max_entry_bytes_per_slot());
        self.apply_cost_tracker_limits_for_active_features();
        self.apply_partitioned_epoch_rewards_config_for_active_features();
    }
```

**File:** runtime/src/slot_params.rs (L123-133)
```rust
pub(crate) const LEGACY_SLOT_PARAMS: SlotParams = SlotParams {
    ns_per_slot: 400_000_000,
    slots_per_year: 78_892_314.984,
    hashes_per_tick: Some(LEGACY_HASHES_PER_TICK),
    cost_tracker_limits: CostTrackerLimits::new(24_000_000, 60_000_000, 100_000_000),
    max_data_shreds_per_slot: 32_768,
    max_code_shreds_per_slot: 32_768,
    max_entry_bytes_per_slot: 20 * 1024 * 1024,
    partitioned_epoch_rewards_stake_account_stores_per_block: 4096,
    vat_to_burn_per_epoch: 1_600_000_000,
};
```
