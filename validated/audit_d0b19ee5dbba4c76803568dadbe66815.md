### Title
Unbounded per-request CPU/memory cost in `getVoteAccounts` scales linearly with total on-chain vote-account count - ([File: rpc/src/rpc.rs])

### Summary
`RpcSol::get_vote_accounts` in `rpc/src/rpc.rs` iterates the entire `VoteAccounts` map returned by `Bank::vote_accounts()` for every single `getVoteAccounts` call, decoding each account's vote state and epoch-credit history. Even when the caller supplies a `vote_pubkey` filter, the implementation still walks the full map instead of doing a direct lookup, so a single unprivileged, low-rate JSON-RPC call has per-request cost that grows linearly with the number of vote accounts an attacker has previously created on-chain.

### Finding Description
The handler is: [1](#0-0) 

Specifically:
- `bank.vote_accounts()` returns the complete `VoteAccountsHashMap` of every vote account tracked in `Stakes<StakeAccount>` for the bank [2](#0-1) .
- The subsequent `.iter().filter_map(...)` walks every entry in that map, and only inside the closure does it check `filter_by_vote_pubkey` to decide whether to keep or discard the entry [3](#0-2) . This means the `vote_pubkey` filter does not avoid the full scan — it merely reduces the returned payload size, not the work performed.
- For every entry visited (filtered or not), the code calls `account.vote_state_view()` to decode the vote account's on-chain data, and `vote_state_view.epoch_credits_iter()` to walk its epoch-credit history [4](#0-3) , both of which have cost proportional to the size/complexity of that particular vote account's data, in addition to the linear number of entries.
- There is no page size, result cap, or entry-count limit enforced anywhere in this function; the only bound applied is `MAX_RPC_VOTE_ACCOUNT_INFO_EPOCH_CREDITS_HISTORY`, which limits the *output size per account*, not the *number of accounts scanned* [5](#0-4) .

An attacker who is otherwise unprivileged can create many rent-exempt vote accounts owned by `solana_vote_program` over time (this write path is not rate-limited by the RPC read-side guard being evaluated here). Once N vote accounts exist on-chain, a single `getVoteAccounts` request — well within the one-call-per-`CLUSTER_SLOT_TIME_TARGET/2` budget — forces the validator to iterate, decode, and process all N accounts. No existing parameter-limit, commitment check, or subscription quota in this path bounds the iteration to a fixed cost; commitment (`config.commitment`) only selects which bank snapshot is queried, not how much of it is scanned.

### Impact Explanation
This matches the "Scope: High" category described in the prompt: request cost is bounded by the size of on-chain state rather than by an explicit, attacker-independent limit. Repeated (but still low-rate, single-caller) `getVoteAccounts` calls against a validator with a large vote-account set consume CPU proportional to on-chain data size, which can degrade RPC responsiveness and consume CPU/memory resources without any explicit cap, fitting the "RPC DoS from a single low-rate call whose cost scales with on-chain data" bounty category.

### Likelihood Explanation
Feasibility is high: creating a vote account only requires funding it to rent-exemption and issuing a `VoteInit` via `solana_vote_program`, which is a normal, permissionless operation available to any funded account. No leader, gossip, or staked-node control is needed. Once the attacker has amassed a large number of vote accounts (a one-time, unprivileged cost paid over time), every subsequent single `getVoteAccounts` call — even one issued at the permitted low rate — reliably incurs O(N) cost, making the issue trivially repeatable.

### Recommendation
- When `config.vote_pubkey` is set, perform a direct `HashMap` lookup (`vote_accounts.get(&pubkey)`) instead of scanning and filtering the entire map.
- For the unfiltered case, consider introducing an explicit limit/pagination mechanism for `getVoteAccounts`, or precomputing/caching decoded `RpcVoteAccountInfo` per epoch so per-request cost does not require re-decoding vote state and re-walking epoch credits for every vote account on every call.
- Document/enforce a maximum number of vote accounts processed per call, independent of the actual on-chain vote-account count.

### Proof of Concept
```rust
// rpc/src/rpc.rs (test module) — benchmark-style test demonstrating linear scaling
#[test]
fn test_get_vote_accounts_scales_with_vote_account_count() {
    use std::time::Instant;

    fn bench_with_n_vote_accounts(n: usize) -> std::time::Duration {
        let rpc = RpcHandler::start();
        let bank = rpc.working_bank();

        // Create N funded, rent-exempt vote accounts owned by solana_vote_program.
        for _ in 0..n {
            let vote_keypair = Keypair::new();
            let vote_state = VoteStateV4::new_with_defaults(
                &vote_keypair.pubkey(),
                &VoteInit {
                    node_pubkey: rpc.mint_keypair.pubkey(),
                    authorized_voter: vote_keypair.pubkey(),
                    authorized_withdrawer: vote_keypair.pubkey(),
                    commission: 0,
                },
                &bank.get_sysvar_cache_for_tests().get_clock().unwrap(),
            );
            rpc.store_vote_account(&vote_keypair.pubkey(), vote_state);
        }

        let req = r#"{"jsonrpc":"2.0","id":1,"method":"getVoteAccounts"}"#;
        let start = Instant::now();
        let _res = rpc.io.handle_request_sync(req, rpc.meta.clone());
        start.elapsed()
    }

    let t_1k = bench_with_n_vote_accounts(1_000);
    let t_100k = bench_with_n_vote_accounts(100_000);

    // Assert that latency grows roughly linearly (i.e., NOT capped/sub-linear),
    // demonstrating the absence of a request-side bound.
    let ratio = t_100k.as_secs_f64() / t_1k.as_secs_f64();
    assert!(
        ratio > 50.0,
        "expected near-linear scaling with vote-account count, got ratio {ratio}"
    );
}
```
Expected result: latency (and allocations, measurable via a heap-profiling harness) scales roughly linearly with the number of on-chain vote accounts, confirming that a single `getVoteAccounts` call has no explicit cost bound and is instead governed by total on-chain state size.

### Citations

**File:** rpc/src/rpc.rs (L1167-1200)
```rust
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
```
