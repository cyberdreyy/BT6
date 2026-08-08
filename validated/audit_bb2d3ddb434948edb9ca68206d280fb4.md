### Title
Unbounded per-notification block catch-up loop in pubsub `notify_watchers` can stall the shared RPC-notification thread - ([File: rpc/src/rpc_subscriptions.rs])

### Summary
The Allora bug (`SafeApplyFuncOnAllActiveEpochEndingTopics`) is a function invoked on every block (`EndBlocker`) that loops over a caller/state-influenced collection whose size is not properly bounded, causing per-block cost to grow with usage and potentially delaying or halting block production. The closest reachable analog in agave is `RpcSubscriptions::notify_watchers`, which runs once per slot/gossip commitment notification (i.e., on essentially every produced/rooted slot) on the single dedicated pubsub notification thread, and for `SubscriptionParams::Block` subscriptions performs an inner loop whose length is the number of "missed" slots since the last successful notification for that subscription.

### Finding Description
`RpcSubscriptions::process_notifications` runs in one dedicated thread and pulls one `NotificationEntry` at a time from `notification_receiver`, calling `RpcSubscriptions::notify_watchers` synchronously for every `NotificationEntry::Bank` and `NotificationEntry::Gossip` event (i.e., on essentially every slot/commitment update): [1](#0-0) [2](#0-1) 

Inside `notify_watchers`, subscriptions are iterated with `subscriptions.into_par_iter()`, and for `SubscriptionParams::Block(params)` the code computes a range of "slots to notify" from the subscription's last-notified slot up to the current commitment slot, then synchronously fetches and deserializes a full block from the blockstore for every slot in that range: [3](#0-2) 

Critically, `w_last_unnotified_slot` is only advanced when `get_complete_block` succeeds and produces a notifiable update; on error it is deliberately left unchanged "so that it'll retry on the next notification trigger": [4](#0-3) 

This means the size of `slots_to_notify` for a single `blockSubscribe` subscription is unbounded and grows with however many slots have elapsed (or failed to be notified) since the subscription's last successful notification — there is no cap such as "notify at most N slots per pass." Because `notify_watchers` executes on the sole notification-consumer thread before the next queued `NotificationEntry` can be processed, a single lagging/backlogged block subscription forces that thread to synchronously read and deserialize a potentially large, attacker-influenced number of full blocks from the blockstore before any other pending account/signature/log/gossip/root notifications for any other subscriber on the node can be delivered.

This is structurally the same anti-pattern as the Allora finding: a routine invoked on every "block" event, iterating a collection whose bound is a function of application/usage state (here, the gap between successive notifications) rather than a fixed constant, executed on a critical serialized processing path.

### Impact Explanation
An unprivileged JSON-RPC pubsub client can open a `blockSubscribe` subscription (an unprivileged, publicly reachable pubsub API) and then, by causing or waiting for a backlog (e.g., via slow client consumption of the websocket connection making the RPC node's internal bookkeeping fall behind, or via blockstore latency spikes), force the single notification-processing thread to synchronously walk and re-fetch/deserialize a large number of full blocks in one pass of `notify_watchers`. Because this thread also drives notification delivery for every other subscription type (accounts, signatures, logs, programs, roots, slots) on that RPC node, this can delay or throttle notifications for all other unrelated subscribers on the same node — a service-level degradation of the JSON-RPC pubsub subsystem. This does not directly correspond to validator/consensus crash, but it degrades an unprivileged-facing RPC service in a way that scales with usage/backlog rather than being bounded, matching the audit's "computational complexity does not scale correctly with usage growth" concern.

### Likelihood Explanation
Likelihood is moderate: `blockSubscribe` is a standard, unprivileged pubsub subscription type available to any client connected to a node that has pubsub enabled with block-subscription support, and no explicit code enforces an upper bound on the number of slots reprocessed per notification pass for a single subscription. The most likely trigger is a subscriber whose consumption or the node's own I/O falls behind for a period, after which the very next `notify_watchers` pass must catch up over the entire missed range.

### Recommendation
Bound the number of slots processed per `notify_watchers` invocation for `SubscriptionParams::Block`, e.g., cap `slots_to_notify` to a fixed maximum per pass and carry any remainder forward to subsequent passes, rather than attempting to catch up an unbounded backlog synchronously in one iteration on the shared notification thread. Consider also moving expensive per-slot blockstore reads for catch-up notifications off the single serialized notification-consumer thread (e.g., into the existing rayon parallel iteration per subscription, or a dedicated worker) so that one lagging block subscription cannot delay delivery of notifications to other subscribers.

### Proof of Concept
Not independently reproduced; based on static code analysis of `rpc/src/rpc_subscriptions.rs`. A conceptual PoC: subscribe via `blockSubscribe`, then stall the corresponding notifier (e.g., by not reading from the websocket, or by inducing blockstore latency) for many slots, then resume — the next `notify_watchers` call for that subscription will synchronously iterate and fetch every unnotified slot in the accumulated range before any other pending notifications on that same processing pass complete. I was not able to fully verify (within the given tool budget) whether the upstream `notification_sender`/`notification_receiver` channel is bounded or unbounded, which would additionally determine whether sustained backlog also causes unbounded memory growth in the notification queue; this would need further investigation via a live/instrumented run of the pubsub service.

### Citations

**File:** rpc/src/rpc_subscriptions.rs (L763-770)
```rust
        loop {
            if exit.load(Ordering::Relaxed) {
                break;
            }
            match notification_receiver.recv_timeout(Duration::from_millis(RECEIVE_DELAY_MILLIS)) {
                Ok(notification_entry) => {
                    let TimestampedNotificationEntry { entry, queued_at } = notification_entry;
                    match entry {
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

**File:** rpc/src/rpc_subscriptions.rs (L968-1013)
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
```

**File:** rpc/src/rpc_subscriptions.rs (L1029-1047)
```rust
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
```
