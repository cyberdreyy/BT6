# Q4405: Unstake_all reads a stale computed amount - attacker holds most shares

## Question
Can an unprivileged attacker call `unstake_all`, which converts `account.stake_shares` to an amount with the rounded-down helper and then re-derives shares with the rounded-up helper inside `inner_unstake`, while holding the majority of `total_stake_shares`, so rounding accrues mostly to the attacker, breaking the invariant that `unstake_all` burns exactly `account.stake_shares` and leaves no residual shares or NEAR, and leading to direct theft of delegator NEAR from the staking pool?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::inner_unstake`
- Entrypoint: `unstake(amount)` / `unstake_all()` - any delegator
- Attacker controls: the unstaked amount, timing relative to `internal_ping`, and the number of repetitions
- Exploit idea: Call `unstake_all`, which converts `account.stake_shares` to an amount with the rounded-down helper and then re-derives shares with the rounded-up helper inside `inner_unstake`, while holding the majority of `total_stake_shares`, so rounding accrues mostly to the attacker.
- Invariant to test: `unstake_all` burns exactly `account.stake_shares` and leaves no residual shares or NEAR.
- Expected Immunefi impact: Critical - direct theft of delegator NEAR from the staking pool.
- Fast validation: Unit test asserting shares and residual balance are both zero afterwards.
