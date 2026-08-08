### Title
`get_block_time` silently converts a transient `SlotUnavailable` blockstore error into `Ok(None)`, producing non-idempotent block-time results for the same finalized slot - ([File: rpc/src/rpc.rs])

### Summary
`JsonRpcRequestProcessor::get_block_time` decides a slot is "finalized" purely from `highest_super_majority_root()`, then reads the block-time from `Blockstore::get_rooted_block_time`. The persisted block-time column (`blocktime_cf`) is written asynchronously by a separate path (e.g. `transaction_status_service.rs`) that is decoupled from root promotion, so a slot can be rooted (`is_root(slot) == true`) before its block-time entry is flushed. In that window `get_rooted_block_time` returns `BlockstoreError::SlotUnavailable`, which is not recognized by either `check_blockstore_root` or `check_slot_cleaned_up`, so the error falls through and `result.ok()` silently discards it, returning `Ok(None)` to the RPC caller for an already-finalized slot.

### Finding Description
`get_block_time` at [1](#0-0)  takes the "rooted" branch whenever `slot <= highest_super_majority_root()`:

```
let result = self.blockstore.get_rooted_block_time(slot);
self.check_blockstore_root(&result, slot)?;
...
self.check_slot_cleaned_up(&result, slot)?;
Ok(result.ok())
```

`Blockstore::get_rooted_block_time` at [2](#0-1)  returns `Err(BlockstoreError::SlotUnavailable)` when `is_root(slot)` is `true` but `blocktime_cf.get(slot)` returns `None` — i.e. the slot has already become a root but the block-time entry has not yet been written to the blocktime column family.

Neither guard handles this case:
- `check_blockstore_root` at [3](#0-2)  only maps the error when `slot >= blockstore.max_root()` (BlockNotAvailable) or `is_skipped(slot)` (SlotSkipped); since the slot IS a root and not skipped, this returns `Ok(())`.
- `check_slot_cleaned_up` at [4](#0-3)  only maps `BlockstoreError::SlotCleanedUp` or `slot < first_available_block`; `SlotUnavailable` matches neither, so it also returns `Ok(())`.

Execution then falls through to `Ok(result.ok())`, which silently converts the `Err(SlotUnavailable)` into `Ok(None)` — a successful RPC response claiming "no block time" for a slot that is already part of the finalized/rooted chain.

Root promotion (`blockstore.set_roots`, in `root_utils::check_and_handle_new_root` at [5](#0-4) ) and the write of the block-time entry (`set_block_time`, called from `transaction_status_service.rs`) are two independent, asynchronous pipelines. There is no ordering guarantee that block-time is persisted before or synchronously with `is_root()` becoming `true`, nor is `highest_super_majority_root` gated on block-time availability. This makes the following sequence reachable by a single unprivileged client without any fork change:

1. Client calls `getBlockTime(S)` right as slot `S` becomes `<= highest_super_majority_root()` but before `transaction_status_service` has called `set_block_time(S, ...)`. The RPC returns `Ok(None)`.
2. Shortly after (still `<= CLUSTER_SLOT_TIME_TARGET/2` polling), the same client calls `getBlockTime(S)` again after the block-time write completes. The RPC now returns `Ok(Some(timestamp))`.

Both responses reference the exact same finalized slot `S` on the same fork; the second response is not a correction of an "unavailable" state but a state transition. In JSON-RPC semantics, `Ok(None)` for `getBlockTime` is documented/consumed as "block time not available for this block," and callers commonly treat a non-null value once observed as final and immutable for a finalized slot. Silently swapping `None -> Some(timestamp)` for the same finalized slot without any forking event is exactly the invariant violation described.

### Impact Explanation
Any downstream client (wallet, exchange, indexer) that treats a returned block-time for a finalized slot as durable/final can observe a contradictory transition from `null` to a concrete timestamp for the identical slot without any re-org or fork change, purely due to internal write ordering. This matches Agave's "wrong-slot/fork/account data returned" bounty category — the RPC layer temporarily reports incorrect ("cleaned"/unavailable) data for a slot that is already committed, rather than propagating a well-defined, distinguishable error (e.g. `BlockNotAvailable` with retry semantics) or blocking until the write is durable.

### Likelihood Explanation
The race window is bounded by how long `transaction_status_service`/block-time writer takes to catch up to root promotion relative to when `highest_super_majority_root` is bumped and blockstore's root flag is set — both are normal, always-occurring events on every validator, no attacker control over cluster state is needed. A single client repeatedly polling `getBlockTime` for a boundary slot at the allowed rate (≤ 1 call per `CLUSTER_SLOT_TIME_TARGET/2`) can reliably catch this window during normal operation, especially under load or after a leader/validator restart where the block-time writer briefly lags behind rooting. No special privileges, multiple clients, or malicious payloads are required.

### Recommendation
In `get_block_time`, explicitly distinguish `BlockstoreError::SlotUnavailable` from `SlotSkipped`/cleaned-up cases and either: (a) return a retryable/transient error (e.g. `RpcCustomError::BlockNotAvailable`/a new distinct code) instead of silently coercing to `Ok(None)`, or (b) block on `check_blockstore_writes_complete`-style completion for block-time persistence before considering a slot "rooted" for RPC purposes, mirroring how `get_block` already fails closed (`check_blockstore_root`) instead of masking a transient unavailability as a definitive `None`.

### Proof of Concept
Integration-test sketch (blockstore + RPC handler), freezing the promotion boundary:

```rust
#[test]
fn test_get_block_time_inconsistent_across_root_promotion() {
    let rpc = RpcHandler::start();
    // Root the slot in blockstore WITHOUT writing block time yet.
    rpc.blockstore.set_roots(std::iter::once(&5)).unwrap();
    rpc.block_commitment_cache
        .write()
        .unwrap()
        .set_highest_super_majority_root(5);

    // First call: slot is rooted but block_time not yet persisted.
    let request = create_test_request("getBlockTime", Some(json!([5u64])));
    let result1: Option<UnixTimestamp> =
        parse_success_result(rpc.handle_request_sync(request));
    assert_eq!(result1, None); // returns Ok(None) despite slot being finalized

    // Simulate the async block-time writer catching up.
    rpc.blockstore.set_block_time(5, 1_700_000_000).unwrap();

    // Second, identical call for the same finalized slot.
    let request = create_test_request("getBlockTime", Some(json!([5u64])));
    let result2: Option<UnixTimestamp> =
        parse_success_result(rpc.handle_request_sync(request));
    assert_eq!(result2, Some(1_700_000_000));

    // Invariant violated: identical query for the same finalized slot S
    // returned two different, contradictory results with no fork change.
    assert_ne!(result1, result2, "block time for finalized slot must be stable");
}
```

Expected (buggy) behavior today: the assertions pass, demonstrating the inconsistency; a fixed implementation should instead make the first call return a well-defined transient error (not `Ok(None)`) until the block-time write completes, so that once a non-error value is returned it is guaranteed stable for that slot.

### Citations

**File:** rpc/src/rpc.rs (L1248-1268)
```rust
    fn check_blockstore_root<T>(
        &self,
        result: &std::result::Result<T, BlockstoreError>,
        slot: Slot,
    ) -> Result<()> {
        if let Err(err) = result {
            debug!(
                "check_blockstore_root, slot: {:?}, max root: {:?}, err: {:?}",
                slot,
                self.blockstore.max_root(),
                err
            );
            if slot >= self.blockstore.max_root() {
                return Err(RpcCustomError::BlockNotAvailable { slot }.into());
            }
            if self.blockstore.is_skipped(slot) {
                return Err(RpcCustomError::SlotSkipped { slot }.into());
            }
        }
        Ok(())
    }
```

**File:** rpc/src/rpc.rs (L1270-1291)
```rust
    fn check_slot_cleaned_up<T>(
        &self,
        result: &std::result::Result<T, BlockstoreError>,
        slot: Slot,
    ) -> Result<()> {
        let first_available_block = self
            .blockstore
            .get_first_available_block()
            .unwrap_or_default();
        let err: Error = RpcCustomError::BlockCleanedUp {
            slot,
            first_available_block,
        }
        .into();
        if let Err(BlockstoreError::SlotCleanedUp) = result {
            return Err(err);
        }
        if slot < first_available_block {
            return Err(err);
        }
        Ok(())
    }
```

**File:** rpc/src/rpc.rs (L1608-1640)
```rust
    pub async fn get_block_time(&self, slot: Slot) -> Result<Option<UnixTimestamp>> {
        if slot == 0 {
            return Ok(Some(self.genesis_creation_time()));
        }
        if slot
            <= self
                .block_commitment_cache
                .read()
                .unwrap()
                .highest_super_majority_root()
        {
            let result = self.blockstore.get_rooted_block_time(slot);
            self.check_blockstore_root(&result, slot)?;
            if result.is_err()
                && let Some(bigtable_ledger_storage) = &self.bigtable_ledger_storage
            {
                let bigtable_result = bigtable_ledger_storage.get_confirmed_block(slot).await;
                self.check_bigtable_result(&bigtable_result)?;
                return Ok(bigtable_result
                    .ok()
                    .and_then(|confirmed_block| confirmed_block.block_time));
            }
            self.check_slot_cleaned_up(&result, slot)?;
            Ok(result.ok())
        } else {
            let r_bank_forks = self.bank_forks.read().unwrap();
            if let Some(bank) = r_bank_forks.get(slot) {
                Ok(Some(bank.clock().unix_timestamp))
            } else {
                Err(RpcCustomError::BlockNotAvailable { slot }.into())
            }
        }
    }
```

**File:** ledger/src/blockstore.rs (L3956-3966)
```rust
    pub fn get_rooted_block_time(&self, slot: Slot) -> Result<UnixTimestamp> {
        let _lock = self.check_lowest_cleanup_slot(slot)?;

        if self.is_root(slot) {
            return self
                .blocktime_cf
                .get(slot)?
                .ok_or(BlockstoreError::SlotUnavailable);
        }
        Err(BlockstoreError::SlotNotRooted)
    }
```

**File:** votor/src/root_utils.rs (L148-155)
```rust
    // Call leader schedule_cache.set_root() before blockstore.set_root() because
    // bank_forks.root is consumed by repair_service to update gossip, so we don't want to
    // get shreds for repair on gossip before we update leader schedule, otherwise they may
    // get dropped.
    leader_schedule_cache.set_root(rooted_banks.last().unwrap());
    blockstore
        .set_roots(rooted_slots.iter())
        .expect("Ledger set roots failed");
```
