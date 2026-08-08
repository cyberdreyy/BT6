### Title
Unbounded per-notification block backfill cost in `blockSubscribe` allows single-subscription CPU/memory amplification - ([File: rpc/src/rpc_subscriptions.rs])

### Summary
`RpcSubscriptions::notify_watchers` backfills every ancestor slot between a block subscription's `last_notified_slot` and the newly-committed `slot` on each `notify_subscribers` call, fully fetching (`blockstore.get_complete_block`) and encoding (`filter_block_result_txs`) each backfilled block. The number and size of blocks processed per call is unbounded and scales directly with how much on-chain data (transactions) an attacker stuffs into the ancestor slots between two notification triggers, rather than any fixed cap.

### Finding Description
In `notify_watchers`, the `SubscriptionParams::Block` branch computes: [1](#0-0) 

`slots_to_notify` is built as `(*w_last_unnotified_slot..slot)` filtered by ancestry, then `slot` is appended. There is no cap on how large this range can be — it depends entirely on `slot - *w_last_unnotified_slot`, i.e., on how many slots have advanced since the last notification for this particular subscription.

For each `s` in that range (up to `max_complete_transaction_status_slot`), the code calls `blockstore.get_complete_block(s, false)` and `filter_block_result_txs(block, s, params)`: [2](#0-1) 

`get_complete_block` fully reads and deserializes shred/entry data for the slot from the blockstore, reconstructing the entire `VersionedConfirmedBlock` (all transactions, statuses, rewards): [3](#0-2) [4](#0-3) 

`filter_block_result_txs`/`encode_with_options` then perform full JSON encoding of the block's transactions for every slot in the backfilled range (as evidenced by matching encode calls in `rpc.rs`'s `get_block` and the test at rpc_subscriptions.rs:1725-1739 which explicitly builds `confirmed_block.encode_with_options(...)`) — i.e., the same expensive per-block work that `getBlock` does for a single RPC call is repeated once for every backfilled slot in a single `notify_subscribers` invocation.

**Why the attack works despite one subscription and low-rate calls:** The attacker only needs to make ordinary, unprivileged transaction submissions (to their own account) that get included on-chain — this is normal on-chain activity, not a direct API abuse and not restricted by any per-call RPC parameter limit. Because the RPC/pubsub layer notifies watchers per bank-update event (`NotificationEntry::Bank`), and this happens continuously as the cluster produces blocks (driven by validator activity, not by the attacker's calls), `slot - *w_last_unnotified_slot` for a subscriber can grow whenever intervening bank/gossip notifications are delayed, dropped, or coalesced (e.g., under `RECEIVE_DELAY_MILLIS` batching or transient errors per the code's own comment: "notify_watchers is triggered for Slot 1 ... notify_watchers is triggered for Slot 4 ... this will try to fetch blocks for slots 2, 3, and 4"). Each of those backfilled slots, if filled to capacity with attacker transactions, forces a full decode+encode of a maximal block. None of the existing guards — `commitment` filtering, `max_complete_transaction_status_slot` bound (only limits how far forward, not the width of the gap), or `ancestors.contains` filtering (only excludes non-ancestor/forked slots, not size) — bound the aggregate cost of a single `notify_watchers` call for a given subscription.

**Guards checked and found insufficient:**
- `commitment` check only selects which single target `slot` to notify up to; it doesn't limit the backfill window size.
- `max_complete_transaction_status_slot` only prevents notifying slots beyond what's been processed; it doesn't limit how many already-processed slots get backfilled in one pass.
- `ancestors.contains(slot)` filtering only removes slots that are not proper ancestors of the target bank (i.e., filters out competing forks), not gap width.
- There is no explicit `MAX_SLOTS_TO_BACKFILL` or similar bound in this loop.

### Impact Explanation
This causes CPU and memory cost for a single low-rate `blockSubscribe` subscription to scale with the cumulative size of `k` ancestor blocks rather than being bounded by a fixed per-notification limit — matching the "unbounded cost for a single low-rate call" category described in scope. The `subscriptions` `for_each` loop (`subscriptions.into_par_iter()`) runs on the shared `notify_watchers` rayon parallel path used by all subscription types, so a burst of expensive decode/encode work for one subscriber's backfill can consume CPU/memory that also affects the responsiveness of other subscribers processed in the same notification batch, though this remains scoped to the RPC/pubsub subsystem rather than consensus-critical paths.

### Likelihood Explanation
Feasible with a single unprivileged client: subscribe via `blockSubscribe(All)` (or a mentions filter) at `confirmed`/`finalized` commitment, then submit ordinary transactions filling up several ancestor slots to their maximum size using normal transaction submission (no more than one call per `CLUSTER_SLOT_TIME_TARGET/2`, since transaction submission is not itself the throttled RPC call in question). Any natural delay in the subscriber's `Bank` notification delivery (e.g., due to `RECEIVE_DELAY_MILLIS` batching, or the notification channel backing up under load) widens the gap between `w_last_unnotified_slot` and the newly committed `slot`, causing the backfill loop to process more slots in one shot. This is repeatable and does not require any peer/leader/validator-operator control — only on-chain activity plus one subscription.

### Recommendation
Bound the width of `slots_to_notify` (and/or the total cumulative bytes decoded/encoded) per `notify_watchers` invocation for `SubscriptionParams::Block`, e.g., cap the number of backfilled slots processed per call to a small constant and advance `w_last_unnotified_slot` to skip/drop excess backfill (emitting only the most recent block, or an explicit gap-notification), rather than looping over the entire unbounded gap and fully decoding/encoding every ancestor block.

### Proof of Concept
Rust integration test plan (extending the existing `test_check_confirmed_block_subscribe_with_mentions`-style tests in `rpc/src/rpc_subscriptions.rs`):
1. Set up `RpcSubscriptions` with a real `Blockstore` as in `test_pubsub_block_subscribe` (rpc_pubsub_service.rs:530-612).
2. Subscribe once via `blockSubscribe(All, commitment=confirmed)`.
3. Populate blockstore slots `N..N+k` each with the maximum transactions per block (using `populate_blockstore_for_tests`/`create_test_transaction_entries` as in existing tests), without calling `notify_subscribers` for intermediate slots (simulating delayed/coalesced bank notifications).
4. Set `max_complete_transaction_status_slot` to `N+k`.
5. Call `subscriptions.notify_subscribers(CommitmentSlots { slot: N+k, ... })` exactly once.
6. Measure wall-clock time / CPU work spent inside `notify_watchers`'s `Block` branch (e.g., via `Measure::start` already present) and assert that it scales linearly with `k` and per-block size, i.e., grows unboundedly with attacker-controlled on-chain data rather than being capped by a constant regardless of `k`.
7. Assert that `num_blocks_notified` after the single call equals `k+1`, confirming all ancestor blocks were fully decoded/encoded in one notification cycle.

### Citations

**File:** rpc/src/rpc_subscriptions.rs (L986-998)
```rust
                            // as long as they are ancestors of `slot`
                            let mut w_last_unnotified_slot =
                                subscription.last_notified_slot.write().unwrap();
                            // would mean it's the first notification for this subscription connection
                            if *w_last_unnotified_slot == 0 {
                                *w_last_unnotified_slot = slot;
                            }
                            let mut slots_to_notify: Vec<_> =
                                (*w_last_unnotified_slot..slot).collect();
                            let ancestors = bank.proper_ancestors_set();
                            slots_to_notify.retain(|slot| ancestors.contains(slot));
                            slots_to_notify.push(slot);
                            for s in slots_to_notify {
```

**File:** rpc/src/rpc_subscriptions.rs (L1007-1013)
```rust
                                let block_update_result = blockstore
                                    .get_complete_block(s, false)
                                    .map_err(|e| {
                                        error!("get_complete_block error: {e}");
                                        RpcBlockUpdateError::BlockStoreError
                                    })
                                    .and_then(|block| filter_block_result_txs(block, s, params));
```

**File:** ledger/src/blockstore.rs (L4010-4022)
```rust
    pub fn get_complete_block(
        &self,
        slot: Slot,
        require_previous_blockhash: bool,
    ) -> Result<VersionedConfirmedBlock> {
        self.do_get_complete_block_with_components(
            slot,
            require_previous_blockhash,
            /* populate_components */ false,
            /* allow_dead_slots */ false,
        )
        .map(|result| result.block)
    }
```

**File:** ledger/src/blockstore.rs (L4058-4155)
```rust
    fn do_get_complete_block_with_components(
        &self,
        slot: Slot,
        require_previous_blockhash: bool,
        populate_components: bool,
        allow_dead_slots: bool,
    ) -> Result<VersionedConfirmedBlockWithComponents> {
        let Some(slot_meta) = self.meta_cf.get(slot)? else {
            trace!("do_get_complete_block_with_components() failed for {slot} (missing SlotMeta)");
            return Err(BlockstoreError::SlotUnavailable);
        };

        if !slot_meta.is_full() {
            trace!("do_get_complete_block_with_components() failed for {slot} (slot not full)");
            return Err(BlockstoreError::SlotUnavailable);
        }

        let (slot_components, _, _) = self.get_slot_components_with_shred_info(
            slot,
            /*start_index:*/ 0,
            allow_dead_slots,
        )?;

        if slot_components.is_empty() {
            trace!(
                "do_get_complete_block_with_components() failed for {slot} (no components found)"
            );
            return Err(BlockstoreError::SlotUnavailable);
        }

        let blockhash = slot_components
            .iter()
            .rev()
            .find_map(|component| match component {
                BlockComponent::EntryBatch(entries) => entries.last().map(|entry| entry.hash),
                BlockComponent::BlockMarker(_) => None,
            })
            .unwrap_or_else(|| panic!("Rooted slot {slot:?} must have blockhash"));

        let mut starting_transaction_index = 0;
        let mut components = if populate_components {
            Vec::with_capacity(slot_components.len())
        } else {
            Vec::new()
        };

        let slot_transaction_iterator = slot_components
            .into_iter()
            .filter_map(|component| match component {
                BlockComponent::EntryBatch(entries) => {
                    if populate_components {
                        let entry_summaries = entries
                            .iter()
                            .map(|entry| {
                                let entry_summary = EntrySummary {
                                    num_hashes: entry.num_hashes,
                                    hash: entry.hash,
                                    num_transactions: entry.transactions.len() as u64,
                                    starting_transaction_index,
                                };
                                starting_transaction_index += entry.transactions.len();
                                entry_summary
                            })
                            .collect();
                        components.push(ConfirmedBlockComponent::EntryBatch(entry_summaries));
                    }
                    Some(entries)
                }
                BlockComponent::BlockMarker(marker) => {
                    if populate_components {
                        components.push(ConfirmedBlockComponent::BlockMarker(marker));
                    }
                    None
                }
            })
            .flatten()
            .flat_map(|entry| entry.transactions)
            .map(|transaction| {
                if let Err(err) = transaction.sanitize() {
                    warn!(
                        "Blockstore::get_complete_block_with_components sanitize failed: {err:?}, \
                         slot: {slot:?}, {transaction:?}",
                    );
                }
                transaction
            });

        let block = self.build_versioned_confirmed_block(
            slot,
            require_previous_blockhash,
            allow_dead_slots,
            &slot_meta,
            &blockhash,
            slot_transaction_iterator,
        )?;

        Ok(VersionedConfirmedBlockWithComponents { block, components })
    }
```
