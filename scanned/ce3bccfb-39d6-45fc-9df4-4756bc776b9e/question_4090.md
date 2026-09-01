# Q4090: Unstake_all reads a stale computed amount - first delegator

## Question
Can an unprivileged attacker call `unstake_all`, which converts `account.stake_shares` to an amount with the rounded-down helper and then re-derives shares with the rounded-up helper inside `inner_unstake`, as the first delegator, while `total_stake_shares` is still only the seeded `STAKE_SHARE_PRICE_GUARANTEE_FUND`, breaking the invariant that `unstake_all` burns exactly `account.stake_shares` and leaves no residual shares or NEAR, and leading to direct theft of delegator NEAR from the staking pool?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::inner_unstake`
- Entrypoint: `unstake(amount)` / `unstake_all()` - any delegator
- Attacker controls: the unstaked amount, timing relative to `internal_ping`, and the number of repetitions
- Exploit idea: Call `unstake_all`, which converts `account.stake_shares` to an amount with the rounded-down helper and then re-derives shares with the rounded-up helper inside `inner_unstake`, as the first delegator, while `total_stake_shares` is still only the seeded `STAKE_SHARE_PRICE_GUARANTEE_FUND`.
- Invariant to test: `unstake_all` burns exactly `account.stake_shares` and leaves no residual shares or NEAR.
- Expected Immunefi impact: Critical - direct theft of delegator NEAR from the staking pool.
- Fast validation: Unit test asserting shares and residual balance are both zero afterwards.
