### Title
`getVoteAccounts` JSON-RPC handler returns an unbounded array with no pagination, causing unbounded per-call cost as the number of vote accounts grows - (File: rpc/src/rpc.rs)

### Summary
The `getVoteAccounts` RPC method iterates and serializes **every** vote account tracked by the bank's stakes cache on every call, with no limit, cursor, or pagination parameter. Because vote accounts can be created permissionlessly (any account can be initialized as a vote account via the vote program, independent of whether it ever receives stake), the size of this collection is attacker-influenceable and unbounded, mirroring the `GovNFTFactory.govNFTs()` bug class: a getter over an ever-growing registry with no way to iterate partially.

### Finding Description
`JsonRpcRequestProcessor::get_vote_accounts` calls `bank.vote_accounts()`, which returns the full `VoteAccountsHashMap` from the stakes cache [1](#0-0) , then iterates over the *entire* map, building `RpcVoteAccountInfo` for every entry (decoding vote state, epoch credits, etc.) before partitioning into `current`/`delinquent` vectors and returning them as one JSON response [2](#0-1) . Unlike other RPC list-returning endpoints in this file (`getSlotLeaders`, `getBlocksWithLimit`, `getSignaturesForAddress`, `getRecentPerformanceSamples`, `getInflationReward`), which all enforce a maximum limit/size on the returned collection via constants such as `PERFORMANCE_SAMPLES_LIMIT` or `MAX_GET_CONFIRMED_SIGNATURES_FOR_ADDRESS2_LIMIT` [3](#0-2) [4](#0-3) , `getVoteAccounts` has no such bound: it always processes the full set unless the caller opts into filtering by a single `vote_pubkey`.

The only accessor is the RPC method itself; there is no paginated/indexed alternative comparable to the recommended `govNFTsByIndex`/`govNFTs(start, end)` fix, so as the registry (vote account set) grows, callers cannot iterate it incrementally — they must pull the whole set in one unbounded call, exactly the pattern flagged in the external report.

### Impact Explanation
Since vote accounts are created via a standard, permissionless system/vote-program instruction sequence (rent-exempt account creation + vote-program initialization) and do not require any stake to exist, an attacker can inflate the number of vote accounts tracked in `bank.vote_accounts()` over time. Each subsequent `getVoteAccounts` call then does O(n) work (vote-state decoding, epoch-credit history extraction, and full JSON serialization) proportional to the attacker-inflated registry size, with no cap. This causes unbounded CPU/memory cost for a single RPC call, which can degrade or crash the RPC-serving validator process — matching the "unbounded cost for a single low-rate call" acceptance criterion.

### Likelihood Explanation
Likelihood is moderate-to-high: creating vote accounts costs only the rent-exemption minimum per account (no stake required), and `getVoteAccounts` is a standard, unauthenticated JSON-RPC method exposed by any RPC-enabled validator node. A single, low-rate call after the registry has been sufficiently inflated is enough to trigger the unbounded work; no elevated privileges or multiple concurrent requests are needed.

### Recommendation
Add limit/pagination parameters to `RpcGetVoteAccountsConfig` (e.g., `limit`/`offset`, or a maximum cap analogous to `PERFORMANCE_SAMPLES_LIMIT`), and/or enforce a maximum number of entries processed and returned per `getVoteAccounts` call in `JsonRpcRequestProcessor::get_vote_accounts` [2](#0-1) , consistent with limits already applied to other array-returning RPC methods in the same file.

### Proof of Concept
1. Submit many low-cost transactions that create and initialize new vote accounts (rent-exempt minimum balance each, no stake required) against a target RPC node's bank state.
2. As the vote-account set grows into the hundreds of thousands, issue a single `getVoteAccounts` JSON-RPC call.
3. Observe that `get_vote_accounts` [5](#0-4)  must decode vote state and build `RpcVoteAccountInfo` for every account in the map before returning, with no limit, causing CPU/memory usage and response size to scale linearly (unboundedly) with the attacker-controlled registry size — from one JSON-RPC call.

### Citations

**File:** runtime/src/bank.rs (L5796-5799)
```rust
    pub fn vote_accounts(&self) -> Arc<VoteAccountsHashMap> {
        let stakes = self.stakes_cache.stakes();
        Arc::from(stakes.vote_accounts())
    }
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

**File:** rpc/src/rpc.rs (L2533-2539)
```rust
    let limit = limit.unwrap_or(MAX_GET_CONFIRMED_SIGNATURES_FOR_ADDRESS2_LIMIT);

    if limit == 0 || limit > MAX_GET_CONFIRMED_SIGNATURES_FOR_ADDRESS2_LIMIT {
        return Err(Error::invalid_params(format!(
            "Invalid limit; max {MAX_GET_CONFIRMED_SIGNATURES_FOR_ADDRESS2_LIMIT}"
        )));
    }
```

**File:** rpc/src/rpc.rs (L3689-3695)
```rust
            let limit = limit.unwrap_or(PERFORMANCE_SAMPLES_LIMIT);

            if limit > PERFORMANCE_SAMPLES_LIMIT {
                return Err(Error::invalid_params(format!(
                    "Invalid limit; max {PERFORMANCE_SAMPLES_LIMIT}"
                )));
            }
```
