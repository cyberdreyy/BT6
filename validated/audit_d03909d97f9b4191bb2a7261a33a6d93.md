Confirmed: `stakes.rs:check_and_store` at [1](#0-0)  upserts any account owned by the vote program into the `VoteAccounts` cache as soon as `VoteStateVersions::is_correct_size_and_initialized` returns true — there is no minimum-stake or minimum-lamports gate beyond non-zero lamports, and the test at [2](#0-1)  explicitly shows a zero-stake vote account being added to `bank.vote_accounts()`. This is sufficient to answer the question.

### Title
Unbounded, permissionlessly-growable loop over `bank.vote_accounts()` in `getVoteAccounts` causes CPU-cost DoS - (File: `rpc/src/rpc.rs`)

### Summary
The `getVoteAccounts` JSON-RPC handler iterates over the entire in-memory set of vote accounts on every call, with no cap on how many accounts can be processed. Any account owner can permissionlessly create a vote account (paying only the rent-exempt reserve, no minimum stake required), and that account is unconditionally added to the bank-wide `vote_accounts()` map used by this RPC. This mirrors the reported bug class: an attacker-controlled, cheaply-grown list is iterated in full inside a single call, so the cost of that call scales with attacker-controlled state rather than any per-call limit.

### Finding Description
`JsonRpcRequestProcessor::get_vote_accounts` calls `bank.vote_accounts()` and then iterates every entry with `.iter().filter_map(...)`, building `RpcVoteAccountInfo` for each vote account, including deserializing vote state, computing `last_voted_slot()`, `num_epoch_credits()`, and collecting `epoch_credits_iter()`: [3](#0-2) 

`bank.vote_accounts()` returns the full `VoteAccountsHashMap` from the stakes cache with no size limit: [4](#0-3) 

Critically, the underlying cache admits vote accounts regardless of stake. In `StakesCache::check_and_store`, any account owned by the vote program that is correctly sized and initialized is upserted into `vote_accounts`, gated only on non-zero lamports (i.e., the rent-exempt reserve for the account, not any stake requirement): [5](#0-4) 

The test suite explicitly documents that a vote account with zero delegated stake is included in `bank.vote_accounts()`: [6](#0-5) 

Unlike other list-based RPC handlers in this codebase (`getMultipleAccounts`, `getSignatureStatuses`, `simulateTransaction` accounts), which explicitly cap the number of caller-supplied items per request (`MAX_MULTIPLE_ACCOUNTS`, `MAX_GET_SIGNATURE_STATUSES_QUERY_ITEMS`, etc., see e.g. [7](#0-6)  and [8](#0-7) ), `getVoteAccounts` has no such bound — the cost is driven entirely by the size of on-chain state, which is permissionlessly and cheaply expandable by creating many low/zero-stake vote accounts.

### Impact Explanation
Since anyone can create a vote account for the cost of a rent-exempt reserve deposit (no minimum stake, no allow-listing, unlike the report's custodian scenario which at least requires DAO allow-listing), an attacker can inflate the vote-account set to a large size over time. Once inflated, every subsequent `getVoteAccounts` call performs O(n) work (vote-state deserialization + epoch-credit collection per account) on the RPC-serving thread pool, directly matching the accepted impact category "unbounded cost for a single low-rate call." This degrades RPC responsiveness for legitimate clients and can be triggered repeatedly and cheaply since the state, once created, persists.

### Likelihood Explanation
Likelihood is moderate: creating vote accounts requires paying the rent-exempt reserve for each one (a real but not prohibitive cost per account, unlike `getProgramAccounts`, which is explicitly called out as excluded scope in the rules and requires a secondary index for the reasonable path). Because the accounts, once created, remain on-chain indefinitely and are not filtered by stake anywhere in `get_vote_accounts`, the cost of the attack is one-time and the resulting DoS effect is persistent across arbitrarily many subsequent single RPC calls.

### Recommendation
Add a configurable/practical cap on the number of vote accounts processed or returned by `getVoteAccounts` (similar to `MAX_MULTIPLE_ACCOUNTS`/`MAX_GET_SIGNATURE_STATUSES_QUERY_ITEMS`), or bound the iteration cost, e.g., by pre-filtering to only vote accounts with non-zero activated stake before deserializing epoch credits, and/or move the heavy per-account work off the synchronous RPC-request path (`spawn_blocking`, pagination). Additionally, monitor and consider imposing an economic or protocol-level ceiling on the total number of vote accounts that can be created and remain resident in `VoteAccounts`.

### Proof of Concept
1. Repeatedly submit `vote_instruction::create_account`-style transactions to create N vote accounts, each funded only with the rent-exempt reserve and zero delegated stake (no `StakeState` delegation needed).
2. Each such account passes `VoteStateVersions::is_correct_size_and_initialized` and is unconditionally inserted into `StakesCache`'s `vote_accounts` map via `check_and_store` ( [9](#0-8) ).
3. Issue a single `getVoteAccounts` JSON-RPC call. `JsonRpcRequestProcessor::get_vote_accounts` ( [10](#0-9) ) iterates all N accounts, deserializing vote state and copying epoch credits for each, with per-call cost scaling linearly (or worse) with N and no request-level cap to reject or paginate the work.

### Citations

**File:** runtime/src/stakes.rs (L99-142)
```rust
        // Zero lamport accounts are not stored in accounts-db
        // and so should be removed from cache as well.
        if account.lamports() == 0 {
            if solana_vote_program::check_id(owner) {
                let _old_vote_account = {
                    let mut stakes = self.0.write().unwrap();
                    stakes.remove_vote_account(pubkey)
                };
            } else if stake_program::check_id(owner) {
                let mut stakes = self.0.write().unwrap();
                stakes.remove_stake_delegation(
                    pubkey,
                    new_rate_activation_epoch,
                    use_fixed_point_stake_math,
                );
            }
            return;
        }
        debug_assert_ne!(account.lamports(), 0u64);
        if solana_vote_program::check_id(owner) {
            if VoteStateVersions::is_correct_size_and_initialized(account.data()) {
                match VoteAccount::try_from(create_account_shared_data(account)) {
                    Ok(vote_account) => {
                        // drop the old account after releasing the lock
                        let _old_vote_account = {
                            let mut stakes = self.0.write().unwrap();
                            stakes.upsert_vote_account(pubkey, vote_account)
                        };
                    }
                    Err(_) => {
                        // drop the old account after releasing the lock
                        let _old_vote_account = {
                            let mut stakes = self.0.write().unwrap();
                            stakes.remove_vote_account(pubkey)
                        };
                    }
                }
            } else {
                // drop the old account after releasing the lock
                let _old_vote_account = {
                    let mut stakes = self.0.write().unwrap();
                    stakes.remove_vote_account(pubkey)
                };
            };
```

**File:** rpc/src/rpc.rs (L1171-1230)
```rust
        let vote_accounts = bank.vote_accounts();
        let epoch_vote_accounts = bank
            .epoch_vote_accounts(bank.get_epoch_and_slot_index(bank.slot()).0)
            .ok_or_else(Error::invalid_request)?;
        let delinquent_validator_slot_distance = config
            .delinquent_slot_distance
            .unwrap_or(DELINQUENT_VALIDATOR_SLOT_DISTANCE);
        let (current_vote_accounts, delinquent_vote_accounts): (
            Vec<RpcVoteAccountInfo>,
            Vec<RpcVoteAccountInfo>,
        ) = vote_accounts
            .iter()
            .filter_map(|(vote_pubkey, (activated_stake, account))| {
                if let Some(filter_by_vote_pubkey) = filter_by_vote_pubkey
                    && *vote_pubkey != filter_by_vote_pubkey
                {
                    return None;
                }

                let vote_state_view = account.vote_state_view();
                let last_vote = vote_state_view.last_voted_slot().unwrap_or(0);
                let num_epoch_credits = vote_state_view.num_epoch_credits();
                let epoch_credits = vote_state_view
                    .epoch_credits_iter()
                    .skip(
                        num_epoch_credits
                            .saturating_sub(MAX_RPC_VOTE_ACCOUNT_INFO_EPOCH_CREDITS_HISTORY),
                    )
                    .map(Into::into)
                    .collect();

                Some(RpcVoteAccountInfo {
                    vote_pubkey: vote_pubkey.to_string(),
                    node_pubkey: vote_state_view.node_pubkey().to_string(),
                    activated_stake: *activated_stake,
                    commission: if commission_rate_in_basis_points {
                        // Derive percent from native bps, clamping to u8::MAX.
                        let bps = vote_state_view.inflation_rewards_commission();
                        bps.div_ceil(100).min(u8::MAX as u16) as u8
                    } else {
                        vote_state_view.commission()
                    },
                    inflation_rewards_commission_bps: Some(if commission_rate_in_basis_points {
                        vote_state_view.inflation_rewards_commission()
                    } else {
                        vote_state_view.commission() as u16 * 100
                    }),
                    root_slot: vote_state_view.root_slot().unwrap_or(0),
                    epoch_credits,
                    epoch_vote_account: epoch_vote_accounts.contains_key(vote_pubkey),
                    last_vote,
                })
            })
            .partition(|vote_account_info| {
                if bank.slot() >= delinquent_validator_slot_distance {
                    vote_account_info.last_vote > bank.slot() - delinquent_validator_slot_distance
                } else {
                    vote_account_info.last_vote > 0
                }
            });
```

**File:** rpc/src/rpc.rs (L3305-3313)
```rust
                let max_multiple_accounts = meta
                    .config
                    .max_multiple_accounts
                    .unwrap_or(MAX_MULTIPLE_ACCOUNTS);
                if pubkey_strs.len() > max_multiple_accounts {
                    return Err(Error::invalid_params(format!(
                        "Too many inputs provided; max {max_multiple_accounts}"
                    )));
                }
```

**File:** rpc/src/rpc.rs (L3781-3785)
```rust
            if signature_strs.len() > MAX_GET_SIGNATURE_STATUSES_QUERY_ITEMS {
                return Box::pin(future::err(Error::invalid_params(format!(
                    "Too many inputs provided; max {MAX_GET_SIGNATURE_STATUSES_QUERY_ITEMS}"
                ))));
            }
```

**File:** rpc/src/rpc.rs (L7774-7787)
```rust
        // Create a vote account with no stake.
        let alice_vote_keypair = Keypair::new();
        let alice_vote_state = VoteStateV4::new_with_defaults(
            &alice_vote_keypair.pubkey(),
            &VoteInit {
                node_pubkey: mint_keypair.pubkey(),
                authorized_voter: alice_vote_keypair.pubkey(),
                authorized_withdrawer: alice_vote_keypair.pubkey(),
                commission: 0,
            },
            &bank.get_sysvar_cache_for_tests().get_clock().unwrap(),
        );
        rpc.store_vote_account(&alice_vote_keypair.pubkey(), alice_vote_state);
        assert_eq!(bank.vote_accounts().len(), 2);
```

**File:** runtime/src/bank.rs (L5794-5799)
```rust
    /// current vote accounts for this bank along with the stake
    ///   attributed to each account
    pub fn vote_accounts(&self) -> Arc<VoteAccountsHashMap> {
        let stakes = self.stakes_cache.stakes();
        Arc::from(stakes.vote_accounts())
    }
```
