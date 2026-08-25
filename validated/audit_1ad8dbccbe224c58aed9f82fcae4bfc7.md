### Title
Vote account commission can be unilaterally set to 100%, allowing validators to expropriate all staking rewards from delegators - (File: `runtime/src/inflation_rewards/mod.rs`)

### Summary
A validator (vote account authorized voter/withdrawer) can set the vote account's commission rate to 100% (10,000 basis points) with no protocol-enforced maximum cap. Because the commission percentage is applied every epoch during reward redemption to split inflation rewards between the voter (validator) and the staker (delegator), a 100% commission value results in the delegator receiving zero staking reward for that epoch, mirroring the reported DAO-commission issue where an unbounded commission parameter lets the fee-taking party seize the entire reward pool.

### Finding Description
Reward distribution for a stake account delegated to a vote account is computed by `commission_split` and `commission_split_preserve_lamports` in `runtime/src/inflation_rewards/mod.rs`. Both functions clamp the commission value to `MAX_BPS` (10,000, i.e. 100%) rather than rejecting or capping it below 100%: [1](#0-0) [2](#0-1) 

At the 100% boundary, the entire reward is routed to the voter and the staker portion is exactly zero: [3](#0-2) 

The commission value used in this split is read directly from the vote account's on-chain state (`vote_state.commission()` or `inflation_rewards_commission()`), which is fetched per-epoch in `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`: [4](#0-3) 

There is no consensus-level maximum below 100% enforced anywhere in this path — the only "cap" is the trivial `.min(MAX_BPS)` clamp that still permits the extreme value of 100%. Unlike the LiquidStakingManager's `_updateDAORevenueCommission`, which is at least gated by `require(_commissionPercentage <= MODULO, ...)` (still allowing 100%), Agave's vote program similarly allows the commission-setting party (the validator) to reach the 100% extreme with no additional guardrail, and this value is applied automatically and irrevocably to all stake delegated to that vote account for the epoch in which it takes effect.

### Impact Explanation
Any delegator who has staked to a vote account is exposed to having their entire epoch's inflation reward diverted to the validator if the validator sets commission to 100%. Because reward distribution happens automatically as part of epoch-boundary processing (not a delegator-initiated claim they can block), delegators cannot prevent the loss once the commission is in effect for the reward epoch. This is a direct unauthorized transfer of expected stake-reward funds from the staker to the voter, matching the "Impact: unauthorized fund... mutation" criteria.

### Likelihood Explanation
Likelihood is bounded by governance/social factors (validators who abuse this destroy their reputation and lose future delegations), similar to the original finding's caveat about DAO compromise. However, the underlying code path imposes no technical barrier: any vote account authority can call the commission-update vote instruction and set it to the maximum value, and delay-commission-update logic (`delay_commission_updates` feature) only defers the effect by one epoch rather than preventing the 100% value itself, per `runtime/src/bank/partitioned_epoch_rewards/calculation.rs` lines 703-724 cited above.

### Recommendation
Enforce a protocol-level maximum commission rate (e.g., well below 100%) in the vote program's commission-update instruction handler, or require a multi-epoch grace/notice period long enough for delegators to undelegate before a large commission increase takes effect, rather than relying solely on the existing single-epoch delay in `delay_commission_updates`.

### Proof of Concept
1. A validator submits a vote-program instruction to update its vote account's commission to 100% (10,000 bps or legacy `100u8`).
2. At the next reward epoch, `calculate_stake_rewards_and_commissions` reads this commission via `vote_state.commission()` / `inflation_rewards_commission()` (`runtime/src/bank/partitioned_epoch_rewards/calculation.rs:709-724`).
3. `commission_split`/`commission_split_preserve_lamports` (`runtime/src/inflation_rewards/mod.rs:377-435`) compute `(voter_rewards, staker_rewards) = (rewards, 0)` for the 100% case, confirmed by the test assertions at `runtime/src/inflation_rewards/mod.rs:1304-1309`.
4. Every stake account delegated to that vote account receives `staker_rewards = 0` for that epoch, while the validator's commission account receives the full reward amount.

### Citations

**File:** runtime/src/inflation_rewards/mod.rs (L377-382)
```rust
fn commission_split(commission_bps: u16, on: u64) -> (u64, u64, bool) {
    const MAX_BPS: u16 = 10_000;
    const MAX_BPS_U128: u128 = MAX_BPS as u128;
    match commission_bps.min(MAX_BPS) {
        0 => (0, on, false),
        MAX_BPS => (on, 0, false),
```

**File:** runtime/src/inflation_rewards/mod.rs (L416-418)
```rust
    match commission_bps.min(MAX_BPS) {
        0 => (0, on, false),
        MAX_BPS => (on, 0, false),
```

**File:** runtime/src/inflation_rewards/mod.rs (L1304-1309)
```rust
        // 100% commission (10,000 bps)
        assert_eq!(commission_split(10_000, 1), (1, 0, false));
        assert_eq!(commission_split(10_000, 10), (10, 0, false));
        assert_eq!(commission_split(10_000, 100), (100, 0, false));
        assert_eq!(commission_split(10_000, 1_000), (1_000, 0, false));
        assert_eq!(commission_split(10_000, u64::MAX), (u64::MAX, 0, false));
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L709-724)
```rust
        let commission_bps = if delay_commission_updates {
            let vote_state_for_commission = snapshot_epoch_vote_accounts
                .and_then(|eva| eva.get(&vote_pubkey))
                .or_else(|| rewarded_epoch_vote_accounts.and_then(|eva| eva.get(&vote_pubkey)))
                .map(|vote_account| vote_account.vote_state_view())
                .unwrap_or(vote_state);
            if commission_rate_in_basis_points {
                vote_state_for_commission.inflation_rewards_commission()
            } else {
                vote_state_for_commission.commission() as u16 * 100
            }
        } else if commission_rate_in_basis_points {
            vote_state.inflation_rewards_commission()
        } else {
            vote_state.commission() as u16 * 100
        };
```
