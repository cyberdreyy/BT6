# Q3006: Total_staked_balance decreases less than the credit - u128::MAX

## Question
Can an unprivileged attacker exploit `inner_unstake` subtracting the rounded-down `unstake_amount` from `total_staked_balance` while crediting the rounded-up `receive_amount` to the account, with `amount = u128::MAX` so the U256 product dwarfs any real balance, breaking the invariant that sum of all account claims after the call equals the pool's obligations before it, and leading to protocol insolvency: recorded claims exceed the NEAR the contract actually holds?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::inner_unstake`
- Entrypoint: `unstake(amount)` / `unstake_all()` - any delegator
- Attacker controls: the unstaked amount, timing relative to `internal_ping`, and the number of repetitions
- Exploit idea: Exploit `inner_unstake` subtracting the rounded-down `unstake_amount` from `total_staked_balance` while crediting the rounded-up `receive_amount` to the account, with `amount = u128::MAX` so the U256 product dwarfs any real balance.
- Invariant to test: Sum of all account claims after the call equals the pool's obligations before it.
- Expected Immunefi impact: Critical - protocol insolvency: recorded claims exceed the NEAR the contract actually holds.
- Fast validation: Sim many unstakes and compare the sum of claims against `last_total_balance`.
