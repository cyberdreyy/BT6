### Title
`get_vote_accounts` has no bound on the number of vote accounts serialized per call, causing unbounded work proportional to total on-chain vote-account count - ([File: rpc/src/rpc.rs])

### Finding Description
`JsonRpcRequestProcessor::get_vote_accounts` (`rpc/src/rpc.rs`, function starting at line 1155) fetches `bank.vote_accounts()` [1](#0-0)  and, unless the caller supplies `vote_pubkey` to filter to a single account, iterates and serializes **every** vote account currently cached in `Stakes` via `.iter().filter_map(...)` into `RpcVoteAccountInfo` structs [2](#0-1) . The only bound present is `MAX_RPC_VOTE_ACCOUNT_INFO_EPOCH_CREDITS_HISTORY`, which caps the epoch-credits history *per account*, not the number of accounts returned [3](#0-2) . `Bank::vote_accounts()` returns the full `VoteAccountsHashMap` from the `StakesCache`, which tracks every vote-program-owned account observed on-chain (not just those with active stake) [4](#0-3) .

By contrast, the sibling RPC method `get_inflation_reward` explicitly caps the number of addresses processed per call with `MAX_GET_INFLATION_REWARD_ADDRESSES`, rejecting oversized requests before doing any work [5](#0-4) . No equivalent check exists for `get_vote_accounts` in either the trait definition or `meta.get_vote_accounts(config)` dispatch [6](#0-5) .

An unprivileged attacker can, over time (subject to normal transaction/fee rate limits, not the RPC call-rate limit), create the maximum number of vote accounts allowed by rent-exemption/space rules on the target cluster (each vote account only requires rent-exempt lamports and a `VoteInit`, no delegated stake required for the account to exist and be cached). Once N such accounts exist, a single `getVoteAccounts` call with no `vote_pubkey` filter forces the validator to iterate, deserialize `VoteStateView`, and serialize O(N) `RpcVoteAccountInfo` entries synchronously within the JSON-RPC request thread, with no explicit response-size or account-count cap.

### Impact Explanation
This falls into the "unbounded cost for a single low-rate call" category: a single `getVoteAccounts` RPC request can force CPU/memory work and JSON serialization proportional to the total number of vote accounts on-chain rather than any documented/enforced cap, unlike `get_inflation_reward` which enforces `MAX_GET_INFLATION_REWARD_ADDRESSES`. On a node with a very large vote-account count, this increases per-call latency and memory allocation for RPC responses; it is a resource-consumption/DoS-adjacent issue localized to the RPC-serving thread, not a consensus or state-integrity issue.

### Likelihood Explanation
Feasibility requires the attacker to first fund and create many vote accounts, each costing a rent-exempt reserve (no delegated stake required), which is a permitted "write on-chain data later returned through the API" precondition. The number the attacker can create is bounded only by attacker capital and cluster rent/space rules, not by any protocol-level cap on vote-account count. Once created, a single, low-rate `getVoteAccounts` call (default parameters, one call per allowed interval) triggers the full-iteration cost every time, making the issue repeatable indefinitely at minimal per-call cost. This is a real but capital-gated precondition; unlike a pure zero-cost attack, it requires sustained investment to reach a fleet size large enough to matter.

### Recommendation
Add an explicit cap on the number of vote accounts processed/returned per `getVoteAccounts` call (e.g., a `MAX_GET_VOTE_ACCOUNTS` style constant analogous to `MAX_GET_INFLATION_REWARD_ADDRESSES`), or require pagination/limit parameters, and reject/paginate calls that would exceed it, mirroring the input-count guard already used in `get_inflation_reward`.

### Proof of Concept
Rust integration test plan (extending existing `test_get_vote_accounts` harness in `rpc/src/rpc.rs`, using `RpcHandler::start()` and `rpc.store_vote_account`):
```rust
#[test]
fn test_get_vote_accounts_unbounded_cost() {
    let rpc = RpcHandler::start();
    let bank = rpc.working_bank();

    // Create a large number (N, e.g. 50_000) of zero-stake vote accounts,
    // each funded with rent-exempt minimum lamports for VoteStateV4 space.
    for _ in 0..50_000 {
        let vote_keypair = Keypair::new();
        let vote_state = VoteStateV4::new_with_defaults(
            &vote_keypair.pubkey(),
            &VoteInit {
                node_pubkey: vote_keypair.pubkey(),
                authorized_voter: vote_keypair.pubkey(),
                authorized_withdrawer: vote_keypair.pubkey(),
                commission: 0,
            },
            &bank.get_sysvar_cache_for_tests().get_clock().unwrap(),
        );
        rpc.store_vote_account(&vote_keypair.pubkey(), vote_state);
    }
    assert!(bank.vote_accounts().len() > 40_000);

    let req = r#"{"jsonrpc":"2.0","id":1,"method":"getVoteAccounts"}"#;
    let start = std::time::Instant::now();
    let res = rpc.io.handle_request_sync(req, rpc.meta.clone());
    let elapsed = start.elapsed();

    // Assertion that should hold if a cap exists but currently fails:
    // response size/time should be bounded independent of N.
    assert!(
        elapsed < std::time::Duration::from_millis(50),
        "getVoteAccounts took {elapsed:?} for {} vote accounts — cost scales with N, no cap enforced",
        bank.vote_accounts().len()
    );
}
```
Expected result on current code: the assertion fails (or, absent a hard timing bound, a memory/CPU profiling harness shows linear growth with N and no early rejection), demonstrating the missing `MAX_GET_VOTE_ACCOUNTS`-style guard, in contrast to the guarded `get_inflation_reward` path [7](#0-6) .

### Citations

**File:** rpc/src/rpc.rs (L1167-1174)
```rust
        let bank = self.bank(config.commitment);
        let commission_rate_in_basis_points = bank
            .feature_set
            .is_active(&agave_feature_set::commission_rate_in_basis_points::id());
        let vote_accounts = bank.vote_accounts();
        let epoch_vote_accounts = bank
            .epoch_vote_accounts(bank.get_epoch_and_slot_index(bank.slot()).0)
            .ok_or_else(Error::invalid_request)?;
```

**File:** rpc/src/rpc.rs (L1178-1230)
```rust
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

**File:** rpc/src/rpc.rs (L2951-2958)
```rust
        fn get_vote_accounts(
            &self,
            meta: Self::Metadata,
            config: Option<RpcGetVoteAccountsConfig>,
        ) -> Result<RpcVoteAccountStatus> {
            debug!("get_vote_accounts rpc request received");
            meta.get_vote_accounts(config)
        }
```

**File:** rpc/src/rpc.rs (L4278-4286)
```rust
            debug!(
                "get_inflation_reward rpc request received: {:?}",
                address_strs.len()
            );
            if address_strs.len() > MAX_GET_INFLATION_REWARD_ADDRESSES {
                return Box::pin(future::err(Error::invalid_params(format!(
                    "Too many inputs provided; max {MAX_GET_INFLATION_REWARD_ADDRESSES}"
                ))));
            }
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
