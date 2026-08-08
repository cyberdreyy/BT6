### Title
Unbounded per-slot transaction-log collection is fully cloned on every notification cycle for `logsSubscribe`, allowing an unprivileged client + ordinary transaction traffic to inflate RPC pubsub cost without bound - (File: `runtime/src/bank.rs`, `rpc/src/rpc_subscriptions.rs`)

### Summary
This is the closest in-scope analog to the `MultiRangeHook` bug class: a permissionless action (creating many ranges) grows an unbounded collection that is then iterated in full on every ordinary "swap" (hot-path) operation, with cost paid by everyone using the pool. In Agave, an unprivileged `logsSubscribe` client sets the bank's `TransactionLogCollectorFilter` to `All`/`AllWithVotes`, which makes every bank accumulate **all** transaction logs for the slot into an unbounded `Vec<TransactionLogInfo>`. On every commitment/gossip notification tick (the RPC-node hot path that runs continuously as long as any client is subscribed), this entire vector is deep-cloned per subscription in `get_transaction_logs`/`TransactionLogCollector::get_logs_for_address`. Because any unprivileged user can inflate the amount of log data produced in a single slot (via ordinary, permissionless transaction submission up to block limits), and because the clone cost scales with total log bytes in the slot and is repeated for every "All" subscriber and for every commitment level, this produces the same "permissionless growth of an unbounded collection consumed unconditionally in a hot loop" pattern as the reported Solidity bug, on the node's pubsub notification thread.

### Finding Description
`TransactionLogCollector` on the `Bank` accumulates one `TransactionLogInfo` (including the transaction's full `log_messages: Vec<String>`) per transaction whenever the collector filter is `All` or `AllWithVotes`: [1](#0-0) 

That filter is switched to `All`/`AllWithVotes` purely by an unprivileged client subscribing over the public JSON-RPC pubsub `logsSubscribe` method with no address filter — no special permission is required, and every additional such subscription just increments a counter that keeps the collector in "collect everything" mode: [2](#0-1) [3](#0-2) 

Once collection is enabled, retrieval of "All" logs performs a full clone of the entire per-slot vector: [4](#0-3) 

This retrieval, `get_transaction_logs`, is invoked from the RPC subscriptions notification pipeline, `notify_watchers`, once per relevant commitment-slot update for **every** Logs subscription, via `check_commitment_and_notify`: [5](#0-4) [6](#0-5) 

`notify_watchers` itself runs on the RPC node's dedicated notification thread every time `notify_subscribers`/`notify_gossip_subscribers` fires — i.e., continuously, once per new bank/commitment update, for as long as the exit flag isn't set: [7](#0-6) [8](#0-7) 

The parallel structure of `notify_watchers` (`subscriptions.into_par_iter()`) confirms this cost is paid once per subscription, per notification cycle — i.e., an attacker doesn't even need many subscriptions; a single `logsSubscribe(All)` client is enough to force this clone every cycle, and the size of the clone is controlled by ordinary chain users who can maximize per-transaction log output (Solana caps log bytes per transaction, but does not cap the number of transactions per slot processed by a validator up to block limits), directly analogous to `poolLpTokens[poolId]` being iterated in `afterSwap()` regardless of size.

### Impact Explanation
This mirrors the reported bug class precisely: a collection whose size is controlled by ordinary, unprivileged, permissionless activity (transaction submission producing logs) is unconditionally cloned/iterated in a hot path (`notify_watchers`) that executes on every slot/commitment update as long as any client maintains an "All" logs subscription (itself an unprivileged, zero-cost RPC call). The larger the aggregate log volume the network processes in a slot, the more CPU and memory the RPC node's notification thread burns per notification cycle, per such subscriber, indefinitely. This can degrade or stall the pubsub notification pipeline for the RPC node (delaying/dropping notifications for all subscribers, including account/program/signature subscriptions sharing the same commitment-watcher notification cycle), which is a concrete, unbounded-cost-from-low-rate-request condition on the RPC subsystem.

### Likelihood Explanation
Likelihood is moderate-to-high: reaching `TransactionLogCollectorFilter::All` requires nothing more than an unprivileged pubsub `logsSubscribe` call with no `mentions` filter, which is a normal client action many wallets/indexers already perform (subscribing to "all" logs). The amplifying factor (many transactions with heavy logging in one slot) is achievable by any fee-paying, unprivileged user submitting ordinary transactions within a single slot's block limits, requiring no more than the normal single-slot cadence.

### Recommendation
- Avoid cloning the entire `logs: Vec<TransactionLogInfo>` on every notification cycle for `All`/`AllWithVotes` subscribers; instead diff/track only newly-appended logs since the subscriber's `last_notified_slot`, or stream logs incrementally rather than re-cloning the full per-slot buffer.
- Cap the total bytes/number of entries retained in `TransactionLogCollector::logs` per slot regardless of filter mode, and drop/truncate excess with a clear indicator, analogous to enforcing a maximum count in `MultiRangeHook`.
- Consider rate-limiting or accounting the cost of maintaining `All`/`AllWithVotes` logs subscriptions per RPC node (e.g., restricting how many such broad subscriptions are permitted, or requiring configuration opt-in), since a single such subscription forces full-network log collection and cloning regardless of how many other clients are affected.

### Proof of Concept
1. Start an Agave RPC node with pubsub enabled (default configuration).
2. As an unprivileged client, call `logsSubscribe` with `RpcTransactionLogsFilter::All` (no `mentions` address) — see `rpc/src/rpc_pubsub.rs::logs_subscribe`. This flips the node's `TransactionLogCollectorConfig.filter` to `All` for every bank going forward (`rpc/src/rpc_subscription_tracker.rs::LogsSubscriptionsIndex::update_config`).
3. As any other unprivileged, fee-paying user(s), submit many ordinary transactions per slot that each emit near-maximum log output (e.g., programs invoking `msg!` heavily, or many CPI logs), up to the block's compute/transaction limits — no special privilege needed.
4. Observe that on every `notify_subscribers`/`notify_gossip_subscribers` call (i.e., roughly every slot, and again for confirmed/finalized commitment transitions), `notify_watchers` → `check_commitment_and_notify` → `get_transaction_logs` → `Bank::get_logs_for_address(None)` performs `self.logs.clone()` over the full per-slot log vector (`runtime/src/bank.rs` `TransactionLogCollector::get_logs_for_address`), with cost proportional to the total log bytes produced by step 3, repeated for each such subscriber and for each commitment-level notification pass — a cost the RPC node cannot avoid or bound while any `logsSubscribe(All)` client remains connected.

### Citations

**File:** runtime/src/bank.rs (L426-443)
```rust
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TransactionLogInfo {
    pub signature: Signature,
    pub result: Result<()>,
    pub is_vote: bool,
    pub log_messages: TransactionLogMessages,
}

#[derive(Default, Debug)]
pub struct TransactionLogCollector {
    // All the logs collected for from this Bank.  Exact contents depend on the
    // active `TransactionLogCollectorFilter`
    pub logs: Vec<TransactionLogInfo>,

    // For each `mentioned_addresses`, maintain a list of indices into `logs` to easily
    // locate the logs from transactions that included the mentioned addresses.
    pub mentioned_address_map: HashMap<Pubkey, Vec<usize>>,
}
```

**File:** runtime/src/bank.rs (L445-459)
```rust
impl TransactionLogCollector {
    pub fn get_logs_for_address(
        &self,
        address: Option<&Pubkey>,
    ) -> Option<Vec<TransactionLogInfo>> {
        match address {
            None => Some(self.logs.clone()),
            Some(address) => self.mentioned_address_map.get(address).map(|log_indices| {
                log_indices
                    .iter()
                    .filter_map(|i| self.logs.get(*i).cloned())
                    .collect()
            }),
        }
    }
```

**File:** rpc/src/rpc_subscription_tracker.rs (L372-437)
```rust
struct LogsSubscriptionsIndex {
    all_count: usize,
    all_with_votes_count: usize,
    single_count: HashMap<Pubkey, usize>,

    bank_forks: Arc<RwLock<BankForks>>,
}

impl LogsSubscriptionsIndex {
    fn add(&mut self, params: &LogsSubscriptionParams) {
        match params.kind {
            LogsSubscriptionKind::All => self.all_count += 1,
            LogsSubscriptionKind::AllWithVotes => self.all_with_votes_count += 1,
            LogsSubscriptionKind::Single(key) => {
                *self.single_count.entry(key).or_default() += 1;
            }
        }
        self.update_config();
    }

    fn remove(&mut self, params: &LogsSubscriptionParams) {
        match params.kind {
            LogsSubscriptionKind::All => self.all_count -= 1,
            LogsSubscriptionKind::AllWithVotes => self.all_with_votes_count -= 1,
            LogsSubscriptionKind::Single(key) => match self.single_count.entry(key) {
                Entry::Occupied(mut entry) => {
                    *entry.get_mut() -= 1;
                    if *entry.get() == 0 {
                        entry.remove();
                    }
                }
                Entry::Vacant(_) => error!("missing entry in single_count"),
            },
        }
        self.update_config();
    }

    fn update_config(&self) {
        let mentioned_addresses = self.single_count.keys().copied().collect();
        let config = if self.all_with_votes_count > 0 {
            TransactionLogCollectorConfig {
                filter: TransactionLogCollectorFilter::AllWithVotes,
                mentioned_addresses,
            }
        } else if self.all_count > 0 {
            TransactionLogCollectorConfig {
                filter: TransactionLogCollectorFilter::All,
                mentioned_addresses,
            }
        } else {
            TransactionLogCollectorConfig {
                filter: TransactionLogCollectorFilter::OnlyMentionedAddresses,
                mentioned_addresses,
            }
        };

        *self
            .bank_forks
            .read()
            .unwrap()
            .root_bank()
            .transaction_log_collector_config
            .write()
            .unwrap() = config;
    }
}
```

**File:** rpc/src/rpc_pubsub.rs (L482-505)
```rust
    fn logs_subscribe(
        &self,
        filter: RpcTransactionLogsFilter,
        config: Option<RpcTransactionLogsConfig>,
    ) -> Result<SubscriptionId> {
        let params = LogsSubscriptionParams {
            kind: match filter {
                RpcTransactionLogsFilter::All => LogsSubscriptionKind::All,
                RpcTransactionLogsFilter::AllWithVotes => LogsSubscriptionKind::AllWithVotes,
                RpcTransactionLogsFilter::Mentions(keys) => {
                    if keys.len() != 1 {
                        return Err(Error {
                            code: ErrorCode::InvalidParams,
                            message: "Invalid Request: Only 1 address supported".into(),
                            data: None,
                        });
                    }
                    LogsSubscriptionKind::Single(param::<Pubkey>(&keys[0], "mentions")?)
                }
            },
            commitment: config.and_then(|c| c.commitment).unwrap_or_default(),
        };
        self.subscribe(SubscriptionParams::Logs(params))
    }
```

**File:** rpc/src/rpc_subscriptions.rs (L65-81)
```rust
fn get_transaction_logs(
    bank: &Bank,
    params: &LogsSubscriptionParams,
) -> Option<Vec<TransactionLogInfo>> {
    let pubkey = match &params.kind {
        LogsSubscriptionKind::All | LogsSubscriptionKind::AllWithVotes => None,
        LogsSubscriptionKind::Single(pubkey) => Some(pubkey),
    };
    let mut logs = bank.get_transaction_logs(pubkey);
    if matches!(params.kind, LogsSubscriptionKind::All) {
        // Filter out votes if the subscriber doesn't want them
        if let Some(logs) = &mut logs {
            logs.retain(|log| !log.is_vote);
        }
    }
    logs
}
```

**File:** rpc/src/rpc_subscriptions.rs (L750-765)
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
```

**File:** rpc/src/rpc_subscriptions.rs (L908-934)
```rust
    fn notify_watchers(
        max_complete_transaction_status_slot: Arc<AtomicU64>,
        subscriptions: &HashMap<SubscriptionId, Arc<SubscriptionInfo>>,
        bank_forks: &RwLock<BankForks>,
        blockstore: &Blockstore,
        commitment_slots: &CommitmentSlots,
        notifier: &RpcNotifier,
        source: &'static str,
    ) {
        let mut total_time = Measure::start("notify_watchers");

        let num_accounts_found = AtomicUsize::new(0);
        let num_accounts_notified = AtomicUsize::new(0);

        let num_blocks_found = AtomicUsize::new(0);
        let num_blocks_notified = AtomicUsize::new(0);

        let num_logs_found = AtomicUsize::new(0);
        let num_logs_notified = AtomicUsize::new(0);

        let num_programs_found = AtomicUsize::new(0);
        let num_programs_notified = AtomicUsize::new(0);

        let num_signatures_found = AtomicUsize::new(0);
        let num_signatures_notified = AtomicUsize::new(0);

        let subscriptions = subscriptions.into_par_iter();
```

**File:** rpc/src/rpc_subscriptions.rs (L1053-1071)
```rust
                SubscriptionParams::Logs(params) => {
                    num_logs_found.fetch_add(1, Ordering::Relaxed);
                    if let Some(slot) = slot {
                        let notified = check_commitment_and_notify(
                            params,
                            subscription,
                            bank_forks,
                            slot,
                            get_transaction_logs,
                            filter_logs_results,
                            notifier,
                            false,
                        );

                        if notified {
                            num_logs_notified.fetch_add(1, Ordering::Relaxed);
                        }
                    }
                }
```
