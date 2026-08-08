### Title
Unbounded per-notification cost for `programSubscribe` scaling with attacker-controlled program account population - ([File: rpc/src/rpc_subscriptions.rs])

### Summary
`programSubscribe` with default config registers a `ProgramSubscriptionParams` with no filters and no cap on the number of accounts returned per notification. Each commitment-triggered notification cycle calls `bank.get_program_accounts_modified_since_parent(&params.pubkey)` and then `filter_program_results` re-encodes every returned account, so the per-notification CPU/memory cost scales linearly with however many accounts an attacker chooses to create under that program ID, with no RPC-side limit.

### Finding Description
`RpcSolPubSubImpl::program_subscribe` (`rpc/src/rpc_pubsub.rs:449-476`) builds `ProgramSubscriptionParams { pubkey, filters, encoding, data_slice, commitment, with_context }` from client-supplied config [1](#0-0) . `filters` defaults to empty when the client omits them, so no server-side reduction in scanned accounts is enforced beyond whatever the client optionally opts into. On every bank-update-driven notification cycle, `notify_watchers` iterates active subscriptions in parallel and for `SubscriptionParams::Program(params)` calls `check_commitment_and_notify` with the accessor `|bank, params| bank.get_program_accounts_modified_since_parent(&params.pubkey)`, feeding the result into `filter_program_results` [2](#0-1) . `filter_program_results` then iterates over *all* returned `(Pubkey, AccountSharedData)` pairs, applying any (possibly empty) filters and calling `encode_ui_account` per surviving account [3](#0-2) . There is no cap in `ProgramSubscriptionParams` (`rpc/src/rpc_subscription_tracker.rs:164-172`) on the number of accounts a subscription is allowed to enumerate, and no accounts-per-notification limit anywhere in the `notify_watchers`/`filter_program_results` path. Because `get_program_accounts_modified_since_parent` returns every account under that program that changed since the bank's parent, an attacker that owns/controls a program (or just uses a program they deployed) and grows the account set under low-rate transaction submission causes each subsequent notification to scan and re-encode that entire changed set.

### Impact Explanation
This matches the "unbounded cost for a single low-rate call" scoped-impact category: a single `programSubscribe` websocket subscription's per-notification cost (CPU for encoding, memory for the constructed `RpcKeyedAccount` vector) grows proportionally to the number of program-owned accounts the attacker creates on-chain, and this growth is entirely attacker-controlled with no RPC-enforced cap. Because `notify_watchers` runs this work on the validator's own JSON-RPC/pubsub notification thread pool (via `rayon`'s parallel iterator) for every commitment update, sustained growth of the attacker's program account set increases the recurring per-slot cost of servicing that single subscription without bound.

### Likelihood Explanation
Preconditions are minimal and consistent with the unprivileged-attacker model: the attacker needs only to (1) issue one `programSubscribe` call (no special config required — default/no filters is the worst case) and (2) submit ordinary account-creation transactions at any cadence, including well under `CLUSTER_SLOT_TIME_TARGET / 2`, to grow the account count owned by a program they control. No leaked keys, no gossip/validator control, and no multiple RPC calls per slot are needed — this is fully reproducible by any client with basic transaction-submission ability.

### Recommendation
Impose an explicit cap on the number of accounts processed/encoded per program-subscription notification cycle (e.g., truncate or reject when `get_program_accounts_modified_since_parent` results exceed a configured maximum, mirroring the caps that `getProgramAccounts` enforces via secondary-index/account-count limits), and consider requiring at least one filter (data-size or memcmp) for `programSubscribe` subscriptions above a configured account-count threshold, so a single subscription's cost cannot be driven arbitrarily high by attacker-authored on-chain account growth.

### Proof of Concept
Integration test plan (extending the existing `test_check_program_subscribe`-style tests in `rpc/src/rpc_subscriptions.rs`):
1. Create a bank/genesis config and a synthetic `program_id`.
2. In a loop, submit `system_transaction::create_account` transactions assigning increasing numbers of accounts (e.g., 10, 1_000, 100_000) to `program_id`, committing them into a bank.
3. Register a `programSubscribe` with default `RpcProgramAccountsConfig` (no filters) against `program_id` via `RpcSolPubSubImpl::program_subscribe`.
4. Call `subscriptions.notify_subscribers(commitment_slots)` and measure wall-clock time / allocations spent inside `filter_program_results`/`notify_watchers` for each account-count tier using `solana_measure::measure::Measure` (already used in `notify_watchers`).
5. Assert that notification-build time and memory scale roughly linearly with account count and exceed a fixed budget once account count crosses a threshold (e.g., >10k accounts), demonstrating the absence of any cap — i.e., assert `measured_time_for_100k_accounts > 50 * measured_time_for_1k_accounts` approximately, or instrument `filter_program_results` to assert the number of accounts processed equals the full attacker-created set with no truncation.

### Citations

**File:** rpc/src/rpc_pubsub.rs (L449-476)
```rust
    fn program_subscribe(
        &self,
        pubkey_str: String,
        config: Option<RpcProgramAccountsConfig>,
    ) -> Result<SubscriptionId> {
        let config = config.unwrap_or_default();
        let mut filters = config.filters.unwrap_or_default();
        if let Err(error) = verify_filters(&filters) {
            return Err(Error {
                code: ErrorCode::InvalidParams,
                message: error.to_string(),
                data: None,
            });
        }
        optimize_filters(&mut filters);
        let params = ProgramSubscriptionParams {
            pubkey: param::<Pubkey>(&pubkey_str, "pubkey")?,
            filters,
            encoding: config
                .account_config
                .encoding
                .unwrap_or(UiAccountEncoding::Binary),
            data_slice: config.account_config.data_slice,
            commitment: config.account_config.commitment.unwrap_or_default(),
            with_context: config.with_context.unwrap_or_default(),
        };
        self.subscribe(SubscriptionParams::Program(params))
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
