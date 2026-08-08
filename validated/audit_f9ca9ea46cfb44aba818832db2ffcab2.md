### Title
Unbounded per-slot serialization cost for `programSubscribe` watchers via `notify_watchers` → `get_program_accounts_modified_since_parent` - ([File: rpc/src/rpc_subscriptions.rs])

### Summary
A single `programSubscribe` subscription causes the pubsub notification thread to fetch and serialize *every* account owned by the subscribed program that was modified in the just-frozen slot, with no cap on the number or size of accounts. Because ordinary bank-freeze events (not attacker RPC calls) drive `notify_watchers`, an attacker who owns a program can inflate this per-slot cost simply by writing to many distinct accounts on-chain in one slot.

### Finding Description
`notify_watchers` iterates all commitment-watching subscriptions in parallel and, for `SubscriptionParams::Program`, invokes `check_commitment_and_notify` with `bank.get_program_accounts_modified_since_parent(&params.pubkey)` as the bank method and `filter_program_results` as the result filter [1](#0-0) . `get_program_accounts_modified_since_parent` simply calls `self.rc.accounts.load_by_program_slot(self.slot(), Some(program_id))`, returning the complete, unfiltered `Vec<KeyedAccountSharedData>` of every account owned by that program touched in the slot — there is no limit on `N` or on account size [2](#0-1) . `filter_program_results` then applies only the subscriber-provided data filters and encodes each surviving account via `encode_ui_account`, and `check_commitment_and_notify` calls `notifier.notify` once per resulting item [3](#0-2) [4](#0-3) . None of these paths impose a maximum on the number of accounts or bytes handled per subscription per slot, unlike `get_filtered_indexed_accounts`, which does support a `byte_limit_for_scan` that aborts a scan when exceeded [5](#0-4) . That limit, however, only applies to the secondary-index `getProgramAccounts` HTTP path in `rpc.rs`, not to `programSubscribe`'s per-slot notification path. Thus an attacker who deploys/owns a program can, within the compute/block-size limits available to any ordinary transaction sender, write to a large number of distinct accounts owned by that program in a single slot; on bank freeze, `notify_watchers` will unconditionally fetch and serialize all of them for every active subscriber of that program, regardless of how infrequently that subscriber issues RPC calls.

### Impact Explanation
This is a resource-exhaustion / unbounded-cost issue: the CPU and memory cost of one `programSubscribe` connection scales with `O(N * account_size)` where `N` is fully attacker-controlled through on-chain writes, not through the rate of RPC calls the attacker makes. Because `notify_watchers` runs on the shared pubsub notification thread pool and iterates subscriptions with `into_par_iter()`, a single expensive Program-subscription entry can consume disproportionate serialization time on every slot, degrading notification throughput for the whole pubsub service. This matches the "RPC subsystem" DoS category under Agave's bounty program, scoped to a single subscription established via one RPC call plus on-chain writes (allowed per the rules, since it doesn't require multiple clients or an unfiltered `getProgramAccounts` call).

### Likelihood Explanation
Feasibility is high and fully within an unprivileged attacker's reach: deploying/owning a program and issuing normal transactions that create/write many accounts owned by that program is standard, permissionless behavior; no special privileges, keys, or validator control are needed. The subscription itself requires exactly one `programSubscribe` call, satisfying the "no more than one call" constraint. The only natural ceiling on `N` is the per-slot compute/account-write limits enforced by the runtime for any ordinary block of transactions — the same limits that apply to any high-throughput program — so this scales with normal cluster usage patterns and is trivially repeatable every slot.

### Recommendation
Introduce an explicit, configurable cap (e.g., a `max_accounts_per_program_notification` or byte-size cap analogous to `byte_limit_for_scan` used in `get_filtered_indexed_accounts`) inside the `SubscriptionParams::Program` branch of `notify_watchers` / `filter_program_results`, and truncate, split across multiple notifications, or reject (with an error notification) subscriptions whose per-slot result set exceeds the cap. Consider requiring `programSubscribe` filters to be present (or applying byte-limit scanning at the bank level, not fetching the full modified-account set at all) to bound the cost independent of on-chain writer behavior.

### Proof of Concept
Rust benchmark/integration test plan (extending the existing `test_check_program_subscribe` pattern at `rpc/src/rpc_subscriptions.rs:1769`):
1. Create a genesis config and `Bank`/`BankForks`, and register one `programSubscribe` for `program_id` with no filters, mirroring `rpc/src/rpc_subscriptions.rs:1801-1820`.
2. In a single slot, issue `N` `system_transaction::create_account` (or custom program) transactions that create `N` distinct accounts owned by `program_id`, varying `N` (e.g., 100, 1,000, 10,000) and account data size.
3. Freeze the bank and call `subscriptions.notify_subscribers(...)` (as in the existing test at line 1833), measuring the wall-clock time of `RpcSubscriptions::notify_watchers`'s `Program` branch via `Measure::start("notify_watchers")` (already instrumented at `rpc/src/rpc_subscriptions.rs:917`).
4. Assert that latency and memory grow linearly/unboundedly with `N` and account size, and that no configured cap (max accounts/bytes per program notification) exists to bound this — i.e., assert the absence of any early termination/truncation in `filter_program_results`/`get_program_accounts_modified_since_parent` for large `N`, confirming the invariant violation.

### Citations

**File:** rpc/src/rpc_subscriptions.rs (L153-171)
```rust
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

**File:** rpc/src/rpc_subscriptions.rs (L410-437)
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
```

**File:** rpc/src/rpc_subscriptions.rs (L1072-1092)
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
                }
```

**File:** runtime/src/bank.rs (L5163-5170)
```rust
    pub fn get_program_accounts_modified_since_parent(
        &self,
        program_id: &Pubkey,
    ) -> Vec<KeyedAccountSharedData> {
        self.rc
            .accounts
            .load_by_program_slot(self.slot(), Some(program_id))
    }
```

**File:** runtime/src/bank/tests.rs (L3470-3502)
```rust
#[test]
fn test_get_filtered_indexed_accounts_limit_exceeded() {
    let (genesis_config, _mint_keypair) = create_genesis_config(500);
    let mut account_indexes = AccountSecondaryIndexes::default();
    account_indexes.indexes.insert(AccountIndex::ProgramId);
    let bank_config = BankTestConfig {
        accounts_db_config: AccountsDbConfig {
            account_indexes: Some(account_indexes),
            ..ACCOUNTS_DB_CONFIG_FOR_TESTING
        },
    };
    let bank = Arc::new(Bank::new_with_paths_for_tests(
        &genesis_config,
        Some(bank_config),
        vec![],
        None,
    ));

    let address = Pubkey::new_unique();
    let program_id = Pubkey::new_unique();
    let limit = 100;
    let account = AccountSharedData::new(1, limit, &program_id);
    bank.store_account(&address, &account);

    assert!(
        bank.get_filtered_indexed_accounts(
            &IndexKey::ProgramId(program_id),
            |_| true,
            Some(limit), // limit here will be exceeded, resulting in aborted scan
        )
        .is_err()
    );
}
```
