Found it — the direct `getSlotLeaders` RPC method enforces `MAX_GET_SLOT_LEADERS` (5000), but `getBlockProduction` internally calls the same `get_slot_leaders()` helper with an unbounded count derived from the caller-supplied `range.first_slot`/`range.last_slot`, without ever checking `MAX_GET_SLOT_LEADERS`.

### Title
Missing Upper Bound on `getBlockProduction` Slot Range Allows Unbounded `get_slot_leaders` Cost - ([File: rpc/src/rpc.rs])

### Summary
`get_block_production` in `rpc/src/rpc.rs` accepts a caller-supplied `range: { firstSlot, lastSlot }` and only checks that the range is within `slot_history.oldest()..slot_history.newest()`, and that `first_slot <= last_slot`. It then calls `meta.get_slot_leaders(commitment, first_slot, count)` with `count = last_slot - first_slot + 1`, with no cap analogous to `MAX_GET_SLOT_LEADERS` (5000) that the sibling `getSlotLeaders` RPC method enforces just a few functions away.

### Finding Description
`get_block_production` computes the allowed range bounds purely from `SlotHistory` (`slot_history.oldest()`/`slot_history.newest()`), which can span up to the full `SlotHistory` bitvec capacity (on the order of hundreds of thousands to over a million slots), not the small `MAX_GET_SLOT_LEADERS = 5000` limit used by the direct `getSlotLeaders` RPC handler: [1](#0-0) 

Contrast this with the sibling handler, which explicitly caps the request: [2](#0-1) 

The `get_slot_leaders` implementation that both paths funnel into allocates a `Vec::with_capacity(limit)` and iterates epoch-by-epoch, computing/fetching leader schedules for every epoch spanned by the requested range: [3](#0-2) 

Because `getBlockProduction`'s `range` is not capped the same way `getSlotLeaders`'s `limit` is, a single unprivileged JSON-RPC call with `range.firstSlot = slot_history.oldest()`, `range.lastSlot = slot_history.newest()` can request slot-leader resolution and per-slot bookkeeping (`HashMap` entries, iteration over `slot_history.check(slot)`) for the entire retained slot-history span in one request/one call — exactly the "missing upper bound" bug class from the report, transplanted from an unbounded vesting-period value to an unbounded per-request slot range/leader-computation cost.

### Impact Explanation
This does not corrupt consensus state or cross-fork data, but it produces unbounded server-side CPU/memory cost from a single low-rate, unprivileged RPC call (`getBlockProduction`), which matches the "unbounded cost for a single low-rate call" acceptance criterion. Depending on the deployment (whether `slot_history` is fully populated, e.g. a long-running validator with a busy `SlotHistory`), a single call can force construction of a `HashMap` entry and `slot_history.check()` lookup for every slot in the retained history, and force `get_slot_leaders` to walk/compute leader schedules across many epochs in one synchronous call.

### Likelihood Explanation
`getBlockProduction` is part of the standard "Full" JSON-RPC surface and requires no special role — any client able to reach the RPC endpoint can supply an arbitrarily wide `range`. The only pre-existing bound (`slot_history.oldest()`/`newest()`) is unrelated to and far looser than the 5000-slot cap intentionally chosen for the twin `getSlotLeaders` endpoint, indicating the omission is a genuine oversight rather than an intentional design choice.

### Recommendation
Apply the same (or a comparably small) upper bound used for `getSlotLeaders` (`MAX_GET_SLOT_LEADERS`, or a dedicated constant) to the `range.first_slot`/`range.last_slot` span accepted by `get_block_production`, rejecting requests with `Error::invalid_params` when `last_slot.saturating_sub(first_slot) + 1` exceeds that bound, mirroring the existing check at [4](#0-3) .

### Proof of Concept
1. Start a node with an RPC endpoint that has retained a large `SlotHistory` (i.e., has been running long enough that `slot_history.oldest()`/`newest()` span far more than 5000 slots).
2. Send a single JSON-RPC request:
```json
{"jsonrpc":"2.0","id":1,"method":"getBlockProduction","params":[{"range":{"firstSlot": <slot_history.oldest()>, "lastSlot": <slot_history.newest()>}}]}
```
3. Observe that the request passes the only existing bound check (`first_slot >= slot_history.oldest()` and `last_slot <= slot_history.newest()`) at [5](#0-4) , and proceeds to call `meta.get_slot_leaders(...)` with `count = last_slot - first_slot + 1`, which can be orders of magnitude larger than the `MAX_GET_SLOT_LEADERS = 5000` cap enforced elsewhere, causing disproportionate CPU/memory work for one request.

### Citations

**File:** rpc/src/rpc.rs (L995-1029)
```rust
    fn get_slot_leaders(
        &self,
        commitment: Option<CommitmentConfig>,
        start_slot: Slot,
        limit: usize,
    ) -> Result<Vec<Pubkey>> {
        let bank = self.bank(commitment);

        let (mut epoch, mut slot_index) =
            bank.epoch_schedule().get_epoch_and_slot_index(start_slot);

        let mut slot_leaders = Vec::with_capacity(limit);
        while slot_leaders.len() < limit {
            if let Some(leader_schedule) =
                self.leader_schedule_cache.get_epoch_leader_schedule(epoch)
            {
                slot_leaders.extend(
                    leader_schedule
                        .get_slot_leaders()
                        .map(|slot_leader| slot_leader.id)
                        .skip(slot_index as usize)
                        .take(limit.saturating_sub(slot_leaders.len())),
                );
            } else {
                return Err(Error::invalid_params(format!(
                    "Invalid slot range: leader schedule for epoch {epoch} is unavailable"
                )));
            }

            epoch += 1;
            slot_index = 0;
        }

        Ok(slot_leaders)
    }
```

**File:** rpc/src/rpc.rs (L3102-3122)
```rust
        fn get_slot_leaders(
            &self,
            meta: Self::Metadata,
            start_slot: Slot,
            limit: u64,
        ) -> Result<Vec<String>> {
            debug!("get_slot_leaders rpc request received (start: {start_slot} limit: {limit})");

            let limit = limit as usize;
            if limit > MAX_GET_SLOT_LEADERS {
                return Err(Error::invalid_params(format!(
                    "Invalid limit; max {MAX_GET_SLOT_LEADERS}"
                )));
            }

            Ok(meta
                .get_slot_leaders(None, start_slot, limit)?
                .into_iter()
                .map(|identity| identity.to_string())
                .collect())
        }
```

**File:** rpc/src/rpc.rs (L3147-3186)
```rust
            let (first_slot, last_slot) = match config.range {
                None => (
                    bank.epoch_schedule().get_first_slot_in_epoch(bank.epoch()),
                    bank.slot(),
                ),
                Some(range) => {
                    let first_slot = range.first_slot;
                    let last_slot = range.last_slot.unwrap_or_else(|| bank.slot());
                    if last_slot < first_slot {
                        return Err(Error::invalid_params(format!(
                            "lastSlot, {last_slot}, cannot be less than firstSlot, {first_slot}"
                        )));
                    }
                    (first_slot, last_slot)
                }
            };

            let Some(slot_history) = bank.get_slot_history() else {
                return Err(RpcCustomError::NoSlotHistory.into());
            };
            if first_slot < slot_history.oldest() {
                return Err(Error::invalid_params(format!(
                    "firstSlot, {}, is too small; min {}",
                    first_slot,
                    slot_history.oldest()
                )));
            }
            if last_slot > slot_history.newest() {
                return Err(Error::invalid_params(format!(
                    "lastSlot, {}, is too large; max {}",
                    last_slot,
                    slot_history.newest()
                )));
            }

            let slot_leaders = meta.get_slot_leaders(
                config.commitment,
                first_slot,
                last_slot.saturating_sub(first_slot) as usize + 1, // +1 because last_slot is inclusive
            )?;
```
