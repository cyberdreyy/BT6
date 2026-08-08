### Title
Permanent stall of `blockSubscribe` last-notified-slot cursor causes unbounded per-notification cost - (File: rpc/src/rpc_subscriptions.rs)

### Summary
`blockSubscribe` notifications advance a per-subscription cursor (`last_notified_slot`) only when a slot's block is successfully encoded. If encoding permanently fails for a slot (e.g. a v0/versioned transaction present while the subscriber never supplied `maxSupportedTransactionVersion`), the cursor never advances, and every subsequent bank/gossip notification recomputes and re-processes an ever-growing range of slots for that same subscription, mirroring the "stuck queue head" bug class from the external report.

### Finding Description
`RpcSubscriptions::notify_watchers` handles `SubscriptionParams::Block` by tracking `subscription.last_notified_slot` and computing, on every notification, the range of not-yet-notified slots to (re)process: [1](#0-0) 

For each slot `s` in that range it calls `blockstore.get_complete_block(s, false)` and then `filter_block_result_txs(block, s, params)`. The cursor `w_last_unnotified_slot` is advanced to `s + 1` **only** in the `Ok(Some(block_update))` branch; on `Err`, the code explicitly does *not* advance the cursor so it "will retry on the next notification trigger": [2](#0-1) 

`filter_block_result_txs` returns a hard `Err(RpcBlockUpdateError::UnsupportedTransactionVersion(version))` whenever the block contains a versioned (non-legacy) transaction and the subscription's `max_supported_transaction_version` is `None`: [3](#0-2) 

This underlying condition is deterministic and immutable per block: the transactions in a finalized/confirmed slot never change, so once a v0 transaction lands in slot `s` and the client subscribed without `maxSupportedTransactionVersion`, `filter_block_result_txs` will return the same `Err` for slot `s` forever. Consequently `w_last_unnotified_slot` is permanently pinned at `s`, and the loop `let mut slots_to_notify: Vec<_> = (*w_last_unnotified_slot..slot).collect();` (line 994 range) recomputes and reprocesses an unboundedly growing range on every future notification, since `slot` (the newly committed/confirmed/finalized slot) keeps increasing while `w_last_unnotified_slot` never does.

This is directly analogous to the reported Liquiditypool bug: a single unprivileged actor's one-time action (a `queueWithdraw`/here, a single `blockSubscribe` request) creates an entry whose processing permanently fails, and the "head" pointer that gates further processing (`queuedWithdrawalHead` / `w_last_unnotified_slot`) never advances, causing unbounded/wasted repeated work on every later processing pass.

### Impact Explanation
A single unprivileged user opens one `blockSubscribe` subscription without specifying `maxSupportedTransactionVersion` (which is the default/legacy client behavior). As soon as any slot in the node's chain contains a version-0 transaction — extremely common in production — that subscription's cursor becomes permanently stuck. From then on, every single `NotificationEntry::Bank`/`NotificationEntry::Gossip` event (fired roughly every slot/confirmation, i.e., very frequently) causes `notify_watchers` to recompute `slots_to_notify` over an ever-widening range and to call `blockstore.get_complete_block` plus re-run block encoding for every slot in that range, all discarded due to the same permanent `Err`. This produces continuously growing CPU/I/O cost (`O(n)` work per notification, `O(n²)` cumulative) on the single-threaded pubsub notification pipeline / rayon pool, from one unprivileged, low-rate action (a single subscribe call), matching the accepted "unbounded cost for a single low-rate call" impact class.

### Likelihood Explanation
High. `maxSupportedTransactionVersion` is optional and many existing/older clients omit it; v0 transactions are common on live networks, so the triggering condition (a v0 tx landing in any watched slot) will occur naturally without any special attacker effort — it does not require malicious crafting, just one ordinary subscription left open.

### Recommendation
When `filter_block_result_txs` (or `get_complete_block`) fails for a slot due to a permanent condition (`UnsupportedTransactionVersion`, as opposed to a possibly-transient `BlockStoreError`), still advance `w_last_unnotified_slot` past that slot instead of retrying it forever, or otherwise cap/bound the growth of `slots_to_notify` (e.g., skip/discard slots older than N, or advance the cursor to `slot - 1` unconditionally, notifying the error once). Distinguish between retryable I/O errors and deterministic encode-level errors so the cursor is never permanently pinned to a slot whose failure will not resolve.

### Proof of Concept
1. Start a validator with pubsub block subscriptions enabled and connect a `blockSubscribe` client without setting `maxSupportedTransactionVersion` in the config.
2. Have any transaction using a v0 (versioned) message land in a subsequent slot `s` that matches the subscription filter (`All` or the mentioned account/program).
3. Observe: `filter_block_result_txs` returns `Err(RpcBlockUpdateError::UnsupportedTransactionVersion(0))` for slot `s` [4](#0-3) ; `w_last_unnotified_slot` is not advanced [5](#0-4) .
4. Continue producing/confirming further slots. On every subsequent `notify_watchers` call the range `(*w_last_unnotified_slot..slot)` grows and is fully recomputed and reprocessed (`get_complete_block` + encode) each time [6](#0-5) , showing the unbounded, ever-increasing per-notification cost for this single stuck subscription.

### Citations

**File:** rpc/src/rpc_subscriptions.rs (L325-358)
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

    let block = ConfirmedBlock::from(block)
        .encode_with_options(
            params.encoding,
            BlockEncodingOptions {
                transaction_details: params.transaction_details,
                show_rewards: params.show_rewards,
                max_supported_transaction_version: params.max_supported_transaction_version,
            },
        )
        .map_err(|err| match err {
            EncodeError::UnsupportedTransactionVersion(version) => {
                RpcBlockUpdateError::UnsupportedTransactionVersion(version)
            }
        })?;
```

**File:** rpc/src/rpc_subscriptions.rs (L985-1049)
```rust
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
```
