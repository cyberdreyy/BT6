# Q5176: Unstake amount equal to the whole pool - paused pool

## Question
Can an unprivileged attacker unstake an amount equal to `total_staked_balance` so the pool's own seeded shares are consumed, while `paused == true`, so `internal_restake` returns early and nothing is re-staked, breaking the invariant that an account can never unstake more than its own shares represent, and leading to direct theft of delegator NEAR from the staking pool?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::inner_unstake`
- Entrypoint: `unstake(amount)` / `unstake_all()` - any delegator
- Attacker controls: the unstaked amount, timing relative to `internal_ping`, and the number of repetitions
- Exploit idea: Unstake an amount equal to `total_staked_balance` so the pool's own seeded shares are consumed, while `paused == true`, so `internal_restake` returns early and nothing is re-staked.
- Invariant to test: An account can never unstake more than its own shares represent.
- Expected Immunefi impact: Critical - direct theft of delegator NEAR from the staking pool.
- Fast validation: Unit test with the full pool amount.
