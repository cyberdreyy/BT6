### Title
Unbounded, permissionlessly-growable vote-account set makes `getVoteAccounts` an O(n) unfiltered scan (RPC single-call DoS) - (File: rpc/src/rpc.rs)

### Summary
`StakesCache::check_and_store` adds *any* account owned by the vote program to the bank-wide `vote_accounts` map as soon as it has non-zero lamports and a correctly-sized, initialized `VoteState`, **regardless of whether any stake is delegated to it**. Creating such an account only requires paying the rent-exempt minimum for a `VoteStateV4`-sized account and issuing a standard `CreateAccount` + vote `Initialize` transaction — a permissionless, cheap, single-transaction action, analogous to `MultiRangeHook.createRange()` being permissionless and appending to `poolLpTokens`. The `getVoteAccounts` JSON-RPC handler in `rpc/src/rpc.rs` then unconditionally iterates the *entire* `bank.vote_accounts()` map on every call — with no limit, pagination, or filter that bounds by delegated stake — analogous to `MultiRangeHook.afterSwap()` looping over the unbounded `poolLpTokens` array.

### Finding Description
`StakesCache::check_and_store` inserts a vote account into the cache whenever the owner is the vote program and the account is correctly sized/initialized, independent of stake: [1](#0-0) 

This is confirmed by `rpc/src/rpc.rs` test `test_get_vote_accounts`, which explicitly creates "a vote account with no stake" and shows the bank's tracked vote-account count grows from 1 to 2 purely from that permissionless creation: [2](#0-1) 

`Bank::vote_accounts()` exposes this entire, unfiltered map: [3](#0-2) 

The `getVoteAccounts` JSON-RPC handler retrieves `bank.vote_accounts()` and iterates every entry with `.iter().filter_map(...)`, deserializing each `VoteStateView`, iterating its epoch-credits history, and building a `RpcVoteAccountInfo` for it, before partitioning into current/delinquent — all in a single unbounded pass with no cap on the number of entries processed: [4](#0-3) 

This mirrors the reported bug class precisely: a permissionless, cheap action (`createRange` ↔ creating a zero/low-stake vote account) grows a shared collection without bound (`poolLpTokens` ↔ `stakes_cache.vote_accounts`), and a routine, unprivileged action later performed by anyone (`afterSwap` ↔ `getVoteAccounts`) must iterate the entire collection with no size limit.

### Impact Explanation
An attacker can submit many cheap transactions (each only needing the vote-program rent-exempt minimum plus a normal transaction fee) that create validly-initialized, zero-or-negligible-stake vote accounts. Because `check_and_store` unconditionally caches any correctly-formed vote account, this permanently inflates `bank.vote_accounts()` for as long as those accounts remain funded above zero lamports. Every subsequent `getVoteAccounts` call by *any* RPC client (not just the attacker) then performs O(n) work over this attacker-inflated set — deserializing vote state, walking `epoch_credits_iter()`, and allocating a `RpcVoteAccountInfo` per account — increasing CPU time and response size for a single unprivileged RPC call. This degrades the RPC node's ability to serve `getVoteAccounts` for all callers and can be used to significantly slow down or exhaust RPC-thread resources on that node, matching the "unbounded cost for a single low-rate call" acceptance criterion.

### Likelihood Explanation
Likelihood is moderate-to-high: creating a vote account requires only standard, unprivileged instructions (`SystemInstruction::CreateAccount` + `VoteInstruction::Initialize...`) and rent-exempt lamports for a `VoteStateV4`-sized account (a few hundredths of a SOL) — no stake delegation, no validator identity approval, and no special permission are required. The `getVoteAccounts` endpoint is part of the standard, always-enabled RPC surface used by wallets, staking UIs, and monitoring tools, so the resulting slow endpoint reliably affects legitimate unprivileged callers once the attacker has seeded enough zero/low-stake vote accounts.

### Recommendation
Bound the cost of `getVoteAccounts` independent of how many vote accounts an attacker can permissionlessly create:
- Filter out (or cap) accounts with zero/negligible delegated stake before the expensive per-account work (vote-state deserialization, epoch-credits iteration, string formatting) is performed, rather than after.
- Consider evicting or refusing to cache vote accounts with no delegated stake in `StakesCache::check_and_store`/`VoteAccounts::insert`, since they contribute nothing to consensus but currently pay the same iteration cost as staked accounts.
- Add a configurable maximum result size / pagination to `getVoteAccounts`, mirroring the `max_multiple_accounts` bound already used for `getMultipleAccounts`.

### Proof of Concept
1. Fund a keypair and repeatedly submit `SystemInstruction::CreateAccount` (rent-exempt lamports, size = `VoteStateV4::size_of()`, owner = vote program) followed by `VoteInstruction::Initialize...` for a fresh keypair, with no `StakeInstruction::DelegateStake` ever issued. Each such account is inserted into `bank.vote_accounts()` per `check_and_store`'s zero-stake-tolerant logic [5](#0-4) .
2. Repeat this thousands/millions of times cheaply (bounded only by rent-exempt lamports and normal fees) to inflate the map size, as directly demonstrated by the existing unit test showing a no-stake vote account is tracked [6](#0-5) .
3. Issue a single `getVoteAccounts` RPC call. The handler unconditionally iterates all entries of the now-inflated map [7](#0-6) , causing CPU time and response payload size to scale with the attacker-controlled vote-account count, degrading service for all other unprivileged callers of that endpoint.

### Citations

**File:** runtime/src/stakes.rs (L117-142)
```rust
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

**File:** rpc/src/rpc.rs (L1155-1246)
```rust
    fn get_vote_accounts(
        &self,
        config: Option<RpcGetVoteAccountsConfig>,
    ) -> Result<RpcVoteAccountStatus> {
        let config = config.unwrap_or_default();

        let filter_by_vote_pubkey = if let Some(ref vote_pubkey) = config.vote_pubkey {
            Some(verify_pubkey(vote_pubkey)?)
        } else {
            None
        };

        let bank = self.bank(config.commitment);
        let commission_rate_in_basis_points = bank
            .feature_set
            .is_active(&agave_feature_set::commission_rate_in_basis_points::id());
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

        let keep_unstaked_delinquents = config.keep_unstaked_delinquents.unwrap_or_default();
        let delinquent_vote_accounts = if !keep_unstaked_delinquents {
            delinquent_vote_accounts
                .into_iter()
                .filter(|vote_account_info| vote_account_info.activated_stake > 0)
                .collect::<Vec<_>>()
        } else {
            delinquent_vote_accounts
        };

        Ok(RpcVoteAccountStatus {
            current: current_vote_accounts,
            delinquent: delinquent_vote_accounts,
        })
    }
```

**File:** rpc/src/rpc.rs (L7772-7787)
```rust
        assert_eq!(bank.vote_accounts().len(), 1);

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
