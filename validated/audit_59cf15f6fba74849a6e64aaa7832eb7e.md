### Title
`programSubscribe` notification flood: unbounded per-slot notification fan-out for a single subscription - ([File: rpc/src/rpc_subscriptions.rs])

### Summary
A single, low-rate `programSubscribe` request causes the validator to emit an unbounded number of individual pub-sub notifications to that same client on every subsequent commitment-slot transition, one notification per account under the program that changed in that slot, with no batching or cap.

### Finding Description
When a client subscribes via `programSubscribe`, the subscription is tracked as `SubscriptionParams::Program` and, on every `NotificationEntry::Bank`/`NotificationEntry::Gossip` event, `notify_watchers` invokes `check_commitment_and_notify` for that subscription [1](#0-0) . That helper calls `bank.get_program_accounts_modified_since_parent(&params.pubkey)` to fetch *all* accounts owned by the program that changed since the parent bank, then iterates the results, sending one `notifier.notify(...)` call per matching account with no cap on the count [2](#0-1) .

The result set is produced by `filter_program_results`, which simply filters/encodes every changed account and returns it as an iterator — there is no threshold, truncation, or aggregation logic [3](#0-2) . Each item in that iterator triggers a separate call into `RpcNotifier::notify`, which serializes a full JSON-RPC notification, pushes it into `recent_items`, and broadcasts it over the `broadcast::Sender<RpcNotification>` channel to the specific subscription's websocket connection [4](#0-3) .

This is the direct analog of the reported `checkToken` cron job bug: a single trigger (there, a 15-day cron tick; here, a single slot-commitment event caused by one subscribe call) walks an unbounded list (`tokenList` there, "all accounts modified under program P" here) and fires one notification per list entry with no aggregation or cap, as literally cited in the report's example (`[...tokensList].map(...) => snap_notify`). Here the equivalent is the `for result in filter_results { notifier.notify(...) }` loop.

### Impact Explanation
Subscribing once to a program that owns a very large number of accounts (e.g., a popular token-program-owned pool set, or any widely used on-chain program) can cause every slot to generate a burst of thousands of individual JSON-serialized notifications to be produced and enqueued for that single client connection. This is unbounded per-slot serialization/broadcast cost driven by a single, low-rate client request (one `programSubscribe` call), consuming CPU (JSON serialization per account, per slot) and memory (`recent_items` buffer growth) on the validator's RPC/pubsub service, and can degrade the pubsub notification thread for all other subscribers sharing that thread, since `process_notifications` runs as a single loop processing entries sequentially [5](#0-4) .

### Likelihood Explanation
Likelihood is high for practical exploitation: an unprivileged client only needs to issue one `programSubscribe` for a program that is known (or can be made, via cheap transactions) to have many accounts change per slot. No special commitment, filters, or elevated access are required, and the fan-out repeats every slot for the life of the subscription.

### Recommendation
Cap or batch the number of per-account notifications sent for a single `Program` subscription notification cycle (e.g., aggregate into a single response or drop/queue excess beyond a threshold as suggested in the referenced report), and/or rate-limit `filter_program_results` output size before invoking `notifier.notify` in the loop at `check_commitment_and_notify` [6](#0-5) .

### Proof of Concept
1. Start a validator/test-validator and connect a pubsub client.
2. Call `programSubscribe` for a program ID that owns (or is made to own, via cheap `system_transaction::create_account`-style calls in a loop, similar to `test_program_subscription`) a very large number of accounts [7](#0-6) .
3. Trigger many account writes under that program within a single slot, then call `bank_forks`/`notify_subscribers` to fire a `NotificationEntry::Bank` event (as in the existing `test_program_subscription` harness, which already demonstrates the single-account case) [8](#0-7) .
4. Observe that `notify_watchers` produces one `RpcNotification` per changed account for that single subscription, with no cap — scaling the notification volume and serialization workload linearly (unbounded) with the number of program-owned accounts touched in that slot [1](#0-0) .

### Citations

**File:** rpc/src/rpc_subscriptions.rs (L136-171)
```rust
fn check_commitment_and_notify<P, S, B, F, X, I>(
    params: &P,
    subscription: &SubscriptionInfo,
    bank_forks: &RwLock<BankForks>,
    slot: Slot,
    bank_method: B,
    filter_results: F,
    notifier: &RpcNotifier,
    is_final: bool,
) -> bool
where
    S: Clone + Serialize,
    B: Fn(&Bank, &P) -> X,
    F: Fn(X, &P, Slot, Arc<Bank>) -> (I, Slot),
    X: Clone + Default,
    I: IntoIterator<Item = S>,
{
    let mut notified = false;
    let bank = bank_forks.read().unwrap().get(slot);
    if let Some(bank) = bank {
        let results = bank_method(&bank, params);
        let mut w_last_notified_slot = subscription.last_notified_slot.write().unwrap();
        let (filter_results, result_slot) =
            filter_results(results, params, *w_last_notified_slot, bank);
        for result in filter_results {
            notifier.notify(
                RpcResponse::from(RpcNotificationResponse {
                    context: RpcNotificationContext { slot },
                    value: result,
                }),
                subscription,
                is_final,
            );
            *w_last_notified_slot = result_slot;
            notified = true;
        }
```

**File:** rpc/src/rpc_subscriptions.rs (L289-322)
```rust
impl RpcNotifier {
    fn notify<T>(&self, value: T, subscription: &SubscriptionInfo, is_final: bool)
    where
        T: serde::Serialize,
    {
        let buf_arc = RPC_NOTIFIER_BUF.with(|buf| {
            let mut buf = buf.borrow_mut();
            buf.clear();
            let notification = Notification {
                jsonrpc: Some(jsonrpc_core::Version::V2),
                method: subscription.method(),
                params: NotificationParams {
                    result: value,
                    subscription: subscription.id(),
                },
            };
            serde_json::to_writer(Cursor::new(&mut *buf), &notification)
                .expect("serialization never fails");
            let buf_str = str::from_utf8(&buf).expect("json is always utf-8");
            Arc::new(String::from(buf_str))
        });

        let notification = RpcNotification {
            subscription_id: subscription.id(),
            json: Arc::downgrade(&buf_arc),
            is_final,
            created_at: Instant::now(),
        };
        // There is an unlikely case where this can fail: if the last subscription is closed
        // just as the notifier generates a notification for it.
        let _ = self.sender.send(notification);

        self.recent_items.lock().unwrap().push(buf_arc);
    }
```

**File:** rpc/src/rpc_subscriptions.rs (L410-438)
```rust
fn filter_program_results(
    accounts: Vec<(Pubkey, AccountSharedData)>,
    params: &ProgramSubscriptionParams,
    last_notified_slot: Slot,
    bank: Arc<Bank>,
) -> (impl Iterator<Item = RpcKeyedAccount> + use<>, Slot) {
    let accounts_is_empty = accounts.is_empty();
    let encoding = params.encoding;
    let filters = params.filters.clone();
    let keyed_accounts = accounts.into_iter().filter(move |(_, account)| {
        filters
            .iter()
            .all(|filter_type| filter_allows(filter_type, account))
    });
    let accounts = if is_known_spl_token_id(&params.pubkey)
        && params.encoding == UiAccountEncoding::JsonParsed
        && !accounts_is_empty
    {
        let accounts = get_parsed_token_accounts(bank, keyed_accounts);
        Either::Left(accounts)
    } else {
        let accounts = keyed_accounts.map(move |(pubkey, account)| RpcKeyedAccount {
            pubkey: pubkey.to_string(),
            account: encode_ui_account(&pubkey, &account, encoding, None, None),
        });
        Either::Right(accounts)
    };
    (accounts, last_notified_slot)
}
```

**File:** rpc/src/rpc_subscriptions.rs (L750-905)
```rust
    fn process_notifications(
        exit: Arc<AtomicBool>,
        max_complete_transaction_status_slot: Arc<AtomicU64>,
        blockstore: Arc<Blockstore>,
        notifier: RpcNotifier,
        notification_receiver: Receiver<TimestampedNotificationEntry>,
        mut subscriptions: SubscriptionsTracker,
        bank_forks: Arc<RwLock<BankForks>>,
        block_commitment_cache: Arc<RwLock<BlockCommitmentCache>>,
        optimistically_confirmed_bank: Arc<RwLock<OptimisticallyConfirmedBank>>,
    ) {
        let mut stats = PubsubNotificationStats::default();

        loop {
            if exit.load(Ordering::Relaxed) {
                break;
            }
            match notification_receiver.recv_timeout(Duration::from_millis(RECEIVE_DELAY_MILLIS)) {
                Ok(notification_entry) => {
                    let TimestampedNotificationEntry { entry, queued_at } = notification_entry;
                    match entry {
                        NotificationEntry::Subscribed(params, id) => {
                            subscriptions.subscribe(params.clone(), id, || {
                                initial_last_notified_slot(
                                    &params,
                                    &bank_forks,
                                    &block_commitment_cache,
                                    &optimistically_confirmed_bank,
                                )
                                .unwrap_or(0)
                            });
                        }
                        NotificationEntry::Unsubscribed(params, id) => {
                            subscriptions.unsubscribe(params, id);
                        }
                        NotificationEntry::Slot(slot_info) => {
                            if let Some(sub) = subscriptions
                                .node_progress_watchers()
                                .get(&SubscriptionParams::Slot)
                            {
                                debug!("slot notify: {slot_info:?}");
                                stats.notify_slot_count += 1;
                                notifier.notify(slot_info, sub, false);
                            }
                        }
                        NotificationEntry::SlotUpdate(slot_update) => {
                            if let Some(sub) = subscriptions
                                .node_progress_watchers()
                                .get(&SubscriptionParams::SlotsUpdates)
                            {
                                debug!("slot update notify: {slot_update:?}");
                                stats.notify_slot_update_count += 1;
                                notifier.notify(slot_update, sub, false);
                            }
                        }
                        // These notifications are only triggered by votes observed on gossip,
                        // unlike `NotificationEntry::Gossip`, which also accounts for slots seen
                        // in VoteState's from bank states built in ReplayStage.
                        NotificationEntry::Vote((vote_pubkey, ref vote_info, signature)) => {
                            if let Some(sub) = subscriptions
                                .node_progress_watchers()
                                .get(&SubscriptionParams::Vote)
                            {
                                let rpc_vote = RpcVote {
                                    vote_pubkey: vote_pubkey.to_string(),
                                    slots: vote_info.slots(),
                                    hash: bs58::encode(vote_info.hash()).into_string(),
                                    timestamp: vote_info.timestamp(),
                                    signature: signature.to_string(),
                                };
                                debug!("vote notify: {vote_info:?}");
                                stats.notify_vote_count += 1;
                                notifier.notify(&rpc_vote, sub, false);
                            }
                        }
                        NotificationEntry::Root(root) => {
                            if let Some(sub) = subscriptions
                                .node_progress_watchers()
                                .get(&SubscriptionParams::Root)
                            {
                                debug!("root notify: {root:?}");
                                stats.notify_root_count += 1;
                                notifier.notify(root, sub, false);
                            }
                        }
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
                        NotificationEntry::SignaturesReceived((slot, slot_signatures)) => {
                            for slot_signature in &slot_signatures {
                                if let Some(subs) = subscriptions.by_signature().get(slot_signature)
                                {
                                    for subscription in subs.values() {
                                        if let SubscriptionParams::Signature(params) =
                                            subscription.params()
                                        {
                                            if params.enable_received_notification {
                                                stats.notify_signature_count += 1;
                                                notifier.notify(
                                                    RpcResponse::from(RpcNotificationResponse {
                                                        context: RpcNotificationContext { slot },
                                                        value: RpcSignatureResult::ReceivedSignature(
                                                            ReceivedSignatureResult::ReceivedSignature,
                                                        ),
                                                    }),
                                                    subscription,
                                                    false,
                                                );
                                            }
                                        } else {
                                            error!("invalid params type in visit_by_signature");
                                        }
                                    }
                                }
                            }
                        }
                    }
                    stats.notification_entry_processing_time_us +=
                        queued_at.elapsed().as_micros() as u64;
                    stats.notification_entry_processing_count += 1;
                }
                Err(RecvTimeoutError::Timeout) => {
                    // not a problem - try reading again
                }
                Err(RecvTimeoutError::Disconnected) => {
                    warn!("RPC Notification thread - sender disconnected");
                    break;
                }
            }
            stats.maybe_submit();
        }
```

**File:** rpc/src/rpc_subscriptions.rs (L1072-1091)
```rust
                SubscriptionParams::Program(params) => {
                    num_programs_found.fetch_add(1, Ordering::Relaxed);
                    if let Some(slot) = slot {
                        let notified = check_commitment_and_notify(
                            params,
                            subscription,
                            bank_forks,
                            slot,
                            |bank, params| {
                                bank.get_program_accounts_modified_since_parent(&params.pubkey)
                            },
                            filter_program_results,
                            notifier,
                            false,
                        );

                        if notified {
                            num_programs_notified.fetch_add(1, Ordering::Relaxed);
                        }
                    }
```

**File:** client-test/tests/client.rs (L358-378)
```rust
    let config = Some(RpcProgramAccountsConfig {
        ..RpcProgramAccountsConfig::default()
    });

    let program_id = Pubkey::new_unique();
    let (mut client, receiver) = PubsubClient::program_subscribe(
        format!("ws://0.0.0.0:{}/", pubsub_addr.port()),
        &program_id,
        config,
    )
    .unwrap();

    // Create new program account at bob's address
    let tx = system_transaction::create_account(&alice, &bob, blockhash, 100, 0, &program_id);
    bank_forks
        .read()
        .unwrap()
        .get(1)
        .unwrap()
        .process_transaction(&tx)
        .unwrap();
```

**File:** client-test/tests/client.rs (L379-416)
```rust
    let commitment_slots = CommitmentSlots {
        slot: 1,
        ..CommitmentSlots::default()
    };
    subscriptions.notify_subscribers(commitment_slots);
    let commitment_slots = CommitmentSlots {
        slot: 2,
        root: 1,
        highest_confirmed_slot: 1,
        highest_super_majority_root: 1,
    };
    subscriptions.notify_subscribers(commitment_slots);

    // Poll notifications generated by the transfer
    let mut notifications = Vec::new();
    let mut pubkeys = HashSet::new();
    loop {
        let response = receiver.recv_timeout(Duration::from_millis(100));
        match response {
            Ok(response) => {
                notifications.push(response.clone());
                pubkeys.insert(response.value.pubkey);
            }
            Err(_) => {
                break;
            }
        }
    }

    // Shutdown
    exit.store(true, Ordering::Relaxed);
    trigger.cancel();
    client.shutdown().unwrap();
    pubsub_service.close().unwrap();

    assert_eq!(notifications.len(), 1);
    assert!(pubkeys.contains(&bob.pubkey().to_string()));
}
```
