# Q4363: Total_staked_balance decreases less than the credit - attacker holds most shares

## Question
Can an unprivileged attacker exploit `inner_unstake` subtracting the rounded-down `unstake_amount` from `total_staked_balance` while crediting the rounded-up `receive_amount` to the account, while holding the majority of `total_stake_shares`, so rounding accrues mostly to the attacker, breaking the invariant that sum of all account claims after the call equals the pool's obligations before it, and leading to protocol insolvency: recorded claims exceed the NEAR the contract actually holds?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::inner_unstake`
- Entrypoint: `unstake(amount)` / `unstake_all()` - any delegator
- Attacker controls: the unstaked amount, timing relative to `internal_ping`, and the number of repetitions
- Exploit idea: Exploit `inner_unstake` subtracting the rounded-down `unstake_amount` from `total_staked_balance` while crediting the rounded-up `receive_amount` to the account, while holding the majority of `total_stake_shares`, so rounding accrues mostly to the attacker.
- Invariant to test: Sum of all account claims after the call equals the pool's obligations before it.
- Expected Immunefi impact: Critical - protocol insolvency: recorded claims exceed the NEAR the contract actually holds.
- Fast validation: Sim many unstakes and compare the sum of claims against `last_total_balance`.
