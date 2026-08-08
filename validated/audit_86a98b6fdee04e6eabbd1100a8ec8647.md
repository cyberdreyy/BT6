### Title
Unbounded per-notification `Vec<Slot>` allocation in `blockSubscribe` when filter never matches - ([File: rpc/src/rpc_subscriptions.rs])

### Summary
`RpcSubscriptions::notify_watchers` computes `slots_to_notify: Vec<_> = (*w_last_unnotified_slot..slot).collect()` for every `Block` subscription on every bank/gossip commitment update [1](#0-0) . If the subscriber uses `RpcBlockSubscribeFilter::MentionsAccountOrProgram(pubkey)` with a pubkey that never appears in any transaction, `filter_block_result_txs` always returns `Ok(None)` [2](#0-1) , which means `w_last_unnotified_slot` is never advanced [3](#0-2) . Consequently the range collected on every subsequent notification grows monotonically with wall-clock/slot progression for the lifetime of a single, otherwise-idle subscription.

### Finding Description
`notify_watchers` is invoked automatically on every bank-freeze/gossip commitment update via the `NotificationEntry::Bank` / `NotificationEntry::Gossip` paths in `process_notifications`, which run continuously as part of normal validator operation and are not gated by additional client requests [4](#0-3) .

For a `SubscriptionParams::Block` entry, the code reads `w_last_unnotified_slot` and, if nonzero, computes:
```
let mut slots_to_notify: Vec<_> = (*w_last_unnotified_slot..slot).collect();
```
before filtering the collected range down with `ancestors.contains(slot)` [5](#0-4) . This `collect()` allocates a `Vec<Slot>` sized by the raw numeric distance between `w_last_unnotified_slot` and the current commitment `slot`, irrespective of how many of those slots are actual ancestors.

`w_last_unnotified_slot` is only advanced to `s + 1` inside the `Ok(Some(block_update))` branch, i.e., only when `filter_block_result_txs` produces an actual notification [6](#0-5) . `filter_block_result_txs` returns `Ok(None)` whenever the block's transactions do not match the subscriber-supplied `BlockSubscriptionKind::MentionsAccountOrProgram(pk)` filter [7](#0-6) . An attacker can supply an arbitrary/unused pubkey (`RpcBlockSubscribeFilter::MentionsAccountOrProgram`) that will never match any transaction. In that case the `None` branch is taken every time, `w_last_unnotified_slot` is never updated, and the value stays frozen at its initial value for the entire lifetime of the subscription.

Since `slot` in the range keeps increasing with the cluster's normal slot cadence (driven by internal replay/gossip events, not attacker requests), every future `notify_watchers` invocation for this same subscription recomputes `(*w_last_unnotified_slot..slot).collect()` over an ever-larger range — the allocation size grows unboundedly with elapsed time from a single subscribe call, with zero additional attacker-issued requests. There is no cap on this collected range size or on how far `slot` may have drifted from `w_last_unnotified_slot`, so the cost of holding one subscription open is not bounded by any explicit limit as required by the invariant.

### Impact Explanation
This falls under "unbounded cost for a single low-rate call": one subscribe request causes the validator's RPC-notification thread to perform a `Vec<Slot>` allocation whose size scales with elapsed slots since subscription — potentially many thousands to millions of `u64` elements the longer the connection stays open — repeated on every subsequent bank/gossip notification (multiple times per second at Solana's slot rate). This causes continuous, growing allocation/CPU churn on the shared notification-processing thread from a single idle subscriber, degrading service for all pubsub clients and, in the worst case, contributing to memory pressure/OOM risk on the RPC node.

### Likelihood Explanation
Highly feasible with a single client action: call `blockSubscribe` once with filter `mentionsAccountOrProgram` set to a pubkey that has never transacted and never will (e.g., a freshly generated keypair) [8](#0-7) , then simply remain connected. No further requests are required — the growth is driven entirely by the validator's own internal per-slot notification cadence, satisfying the "no more than one call per CLUSTER_SLOT_TIME_TARGET/2" constraint trivially (only one call total is needed). The bug is 100% reproducible for any never-matching filter and worsens monotonically over time.

### Recommendation
Bound the notification catch-up window: cap `slot.saturating_sub(*w_last_unnotified_slot)` to a small, fixed maximum (e.g., a constant like `MAX_BLOCK_NOTIFY_CATCHUP_SLOTS`) before calling `.collect()`, and when the true gap exceeds the cap, either reset `w_last_unnotified_slot` to `slot - CAP` (dropping stale unmatched slots) or advance `w_last_unnotified_slot` to `slot` regardless of whether a match was found, so the tracked "last processed" pointer cannot lag indefinitely behind the current slot for filter-mismatch cases.

### Proof of Concept
Integration test plan (Rust, in `rpc/src/rpc_subscriptions.rs` test module, alongside existing `test_check_block_subscribe`-style tests):
1. Set up `BankForks`, `Blockstore`, and `RpcSubscriptions` as in `test_check_finalized_block_subscribe`.
2. Subscribe via `rpc.block_subscribe(RpcBlockSubscribeFilter::MentionsAccountOrProgram(unused_pubkey), config)`.
3. Advance many banks/slots (e.g. 10,000) with transactions that never mention `unused_pubkey`, calling `subscriptions.notify_subscribers(commitment_slots)` (or bank freeze path) after each slot to simulate normal per-slot notification cadence.
4. Instrument/inspect `SubscriptionInfo::last_notified_slot` via a test hook to assert it never advances past its initial value.
5. Add a counting/measuring wrapper (or directly inspect via `#[cfg(test)]` instrumentation) around the `.collect()` call to assert `slots_to_notify.capacity()`/`len()` before the `retain` grows linearly with the number of slots advanced, with no cap — i.e., after N slot advances, `len() == N`, demonstrating unbounded growth tied purely to elapsed slots and not to any explicit per-subscription limit.

### Citations

**File:** rpc/src/rpc_subscriptions.rs (L330-343)
```rust
    block.transactions = match params.kind {
        BlockSubscriptionKind::All => block.transactions,
        BlockSubscriptionKind::MentionsAccountOrProgram(pk) => block
            .transactions
            .into_iter()
            .filter(|tx| tx.account_keys().iter().any(|key| key == &pk))
            .collect(),
    };

    if block.transactions.is_empty()
        && let BlockSubscriptionKind::MentionsAccountOrProgram(_) = params.kind
    {
        return Ok(None);
    }
```

**File:** rpc/src/rpc_subscriptions.rs (L835-862)
```rust
                        NotificationEntry::Bank(commitment_slots) => {
                            const SOURCE: &str = "bank";
                            RpcSubscriptions::notify_watchers(
                                max_complete_transaction_status_slot.clone(),
                                subscriptions.commitment_watchers(),
                                &bank_forks,
                                &blockstore,
                                &commitment_slots,
                                &notifier,
                                SOURCE,
                            );
                        }
                        NotificationEntry::Gossip(slot) => {
                            let commitment_slots = CommitmentSlots {
                                highest_confirmed_slot: slot,
                                ..CommitmentSlots::default()
                            };
                            const SOURCE: &str = "gossip";
                            RpcSubscriptions::notify_watchers(
                                max_complete_transaction_status_slot.clone(),
                                subscriptions.gossip_watchers(),
                                &bank_forks,
                                &blockstore,
                                &commitment_slots,
                                &notifier,
                                SOURCE,
                            );
                        }
```

**File:** rpc/src/rpc_subscriptions.rs (L987-997)
```rust
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
```

**File:** rpc/src/rpc_subscriptions.rs (L1015-1030)
```rust
                                match block_update_result {
                                    Ok(block_update) => {
                                        if let Some(block_update) = block_update {
                                            notifier.notify(
                                                RpcResponse::from(RpcNotificationResponse {
                                                    context: RpcNotificationContext { slot: s },
                                                    value: block_update,
                                                }),
                                                subscription,
                                                false,
                                            );
                                            num_blocks_notified.fetch_add(1, Ordering::Relaxed);
                                            // the next time this subscription is notified it will
                                            // try to fetch all slots between (s + 1) to `slot`, inclusively
                                            *w_last_unnotified_slot = s + 1;
                                        }
```

**File:** rpc-client-types/src/config.rs (L1-1)
```rust
use {
```
