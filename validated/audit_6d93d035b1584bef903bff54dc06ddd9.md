### Title
Unbounded growth of re-fetched slot range in `blockSubscribe` (mentions filter) leads to increasing per-notification cost on the RPC/pubsub node - ([File: rpc/src/rpc_subscriptions.rs])

### Summary
`notify_watchers` in [1](#0-0)  handles `SubscriptionParams::Block` notifications by tracking a per-subscription cursor `last_notified_slot` and, on every new slot, computing `slots_to_notify` as the range from that cursor up to the newly frozen/confirmed slot, then walking that range and calling `blockstore.get_complete_block()` for each slot. The cursor is only advanced (`*w_last_unnotified_slot = s + 1;`) when `filter_block_result_txs` produces `Some(block_update)` — i.e., when the block actually contains a transaction that matches the subscription's `MentionsAccountOrProgram` filter. If no matching transaction is found for a slot (`Ok(None)`), the cursor is silently left unchanged, exactly mirroring the reported bug pattern of a stateful "last update" cursor that fails to advance across iterations, except here the effect is unbounded accumulation rather than an early exit.

### Finding Description
This is structurally analogous to the `RewardsDistributor.rewardRate` bug: a loop-based calculation depends on a persisted cursor (`lastUpdateTime` / here `last_notified_slot`) that is supposed to advance monotonically as time (slots) passes, but the update-condition is coupled to an unrelated business condition (crossing a boost-period boundary / here, whether a matching transaction was found), so the cursor can silently stop advancing while wall-clock/slot time keeps moving forward.

Concretely:
- A client opens a `blockSubscribe` subscription with `BlockSubscriptionKind::MentionsAccountOrProgram(pubkey)` for a `pubkey` that rarely or never appears in transactions.
- `filter_block_result_txs` [2](#0-1)  filters `block.transactions` down to only those referencing `pubkey`; if empty, it returns `Ok(None)`, meaning "nothing to notify for this slot."
- Back in `notify_watchers`, when `Ok(None)` is returned, the code path only sends a notification and advances `w_last_unnotified_slot` inside the `if let Some(block_update) = block_update` branch [3](#0-2) ; when it's `None`, nothing happens and the cursor stays fixed.
- On every subsequent call to `notify_watchers` (which fires on essentially every new slot/commitment update for the whole node), `slots_to_notify` is recomputed as `(*w_last_unnotified_slot..slot)` [4](#0-3) . Because the cursor never moved, this range grows by however many new slots have elapsed since the subscription's cursor last advanced, and grows again on the very next call, and so on — an ever-widening window that is re-walked and re-fetched from `blockstore.get_complete_block()` on every single notification cycle.
- The only bound on this growth is the `break` when `s > max_complete_transaction_status_slot` [5](#0-4) , which merely limits how far into "not yet confirmed" territory the loop goes — it does not cap or reset the accumulated backlog once transaction status catches up.

This produces per-call cost that grows without bound as a function of elapsed slots since the last match, purely from a single, low-privilege `blockSubscribe` call (choosing an account/program pubkey that never appears in transactions, or picking one you know is rare). Each notification cycle re-reads and re-decodes an increasing number of full blocks from the blockstore for that one subscription, which is unprivileged-user-reachable (pubsub subscription, no special role required) and produces genuinely wasted, growing work on the validator/RPC process rather than a one-time bounded cost.

### Impact Explanation
Sustained, unbounded background CPU/I/O cost tied to a single subscribe call: every slot advance re-triggers `get_complete_block` for a monotonically growing range of historical slots for that subscription, and this work is repeated (not cached/advanced) on every subsequent notification. With multiple such subscriptions (or just one long-lived one against a rarely-mentioned account), the additional per-slot workload compounds over the lifetime of the connection, degrading RPC/pubsub node responsiveness for all clients on that node. This matches the "unbounded cost for a single low-rate call" acceptance criterion (a single subscribe call, not a high-rate barrage of RPC requests).

### Likelihood Explanation
Likelihood is high: any unprivileged websocket client can open a `blockSubscribe` request with a `MentionsAccountOrProgram` filter for an account/program that has no (or very infrequent) transaction activity, which is a completely normal, permitted usage pattern requiring no special timing or race condition — the bug triggers on the very first missed slot and compounds automatically as long as the subscription remains open and the pubkey stays "quiet."

### Recommendation
Advance `last_notified_slot` for every processed slot in the loop, regardless of whether a matching block update was found, e.g. `*w_last_unnotified_slot = s + 1;` unconditionally after processing slot `s` (moving it outside the `if let Some(block_update) = block_update` branch), while still preserving the "retry on blockstore error" behavior for the genuine `Err` case only. This ensures the tracked range never grows beyond a small window bounded by the interval between two `notify_watchers` invocations, restoring O(1)-ish per-cycle cost instead of accumulating an unbounded backlog.

### Proof of Concept
Conceptual reproduction (cannot be executed here, but derivable from the cited code paths):
1. Start a node/RPC service with pubsub enabled.
2. Subscribe via `blockSubscribe` with `{"mentionsAccountOrProgram": "<pubkey with no transactions>"}`.
3. Let many slots elapse without any transaction referencing that pubkey.
4. Observe (via added instrumentation or by measuring wall time/CPU of `notify_watchers`'s `Block` branch, or the growing `slots_to_notify.len()` at [4](#0-3) ) that each successive notification cycle processes a monotonically larger range `[last_notified_slot, current_slot)`, and `blockstore.get_complete_block` is called for every slot in that growing range on every cycle, since `last_notified_slot` is never advanced when `filter_block_result_txs` returns `Ok(None)`.

I was unable to fully verify the exact bound of `bank.proper_ancestors_set()` (whether it is capped to recent history or spans the full chain) since I ran out of tool calls before locating its implementation in `runtime/src/bank.rs`; this affects exactly how large the re-fetched range can grow but does not change the fundamental unbounded-cursor-stall bug described above, since the range's lower bound (the stalled cursor) is unrelated to `proper_ancestors_set` and continues to lag further behind `slot` indefinitely.

### Citations

**File:** rpc/src/rpc_subscriptions.rs (L325-343)
```rust
fn filter_block_result_txs(
    mut block: VersionedConfirmedBlock,
    last_modified_slot: Slot,
    params: &BlockSubscriptionParams,
) -> Result<Option<RpcBlockUpdate>, RpcBlockUpdateError> {
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

**File:** rpc/src/rpc_subscriptions.rs (L968-1051)
```rust
                SubscriptionParams::Block(params) => {
                    num_blocks_found.fetch_add(1, Ordering::Relaxed);
                    if let Some(slot) = slot {
                        let bank = bank_forks.read().unwrap().get(slot);
                        if let Some(bank) = bank {
                            // We're calling it unnotified in this context
                            // because, logically, it gets set to `last_notified_slot + 1`
                            // on the final iteration of the loop down below.
                            // This is used to notify blocks for slots that were
                            // potentially missed due to upstream transient errors
                            // that led to this notification not being triggered for
                            // a slot.
                            //
                            // e.g.
                            // notify_watchers is triggered for Slot 1
                            // some time passes
                            // notify_watchers is triggered for Slot 4
                            // this will try to fetch blocks for slots 2, 3, and 4
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
                                // To avoid skipping a slot that fails this condition,
                                // caused by non-deterministic concurrency accesses, we
                                // break out of the loop. Besides if the current `s` is
                                // greater, then any `s + K` is also greater.
                                if s > max_complete_transaction_status_slot.load(Ordering::SeqCst) {
                                    break;
                                }

                                let block_update_result = blockstore
                                    .get_complete_block(s, false)
                                    .map_err(|e| {
                                        error!("get_complete_block error: {e}");
                                        RpcBlockUpdateError::BlockStoreError
                                    })
                                    .and_then(|block| filter_block_result_txs(block, s, params));

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
                                    }
                                    Err(err) => {
                                        // we don't advance `w_last_unnotified_slot` so that
                                        // it'll retry on the next notification trigger
                                        notifier.notify(
                                            RpcResponse::from(RpcNotificationResponse {
                                                context: RpcNotificationContext { slot: s },
                                                value: RpcBlockUpdate {
                                                    slot: s,
                                                    block: None,
                                                    err: Some(err),
                                                },
                                            }),
                                            subscription,
                                            false,
                                        );
                                    }
                                }
                            }
                        }
                    }
```
