# Q5285: Last_total_balance drifts below reality - right after pool creation

## Question
Can an unprivileged attacker make `internal_withdraw` subtract from `last_total_balance` NEAR that never actually left the account, on a pool created moments earlier through the public `create_staking_pool`, breaking the invariant that `last_total_balance == env::account_balance() + env::account_locked_balance()` at the end of every call, and leading to protocol insolvency: recorded claims exceed the NEAR the contract actually holds?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_withdraw`
- Entrypoint: `withdraw(amount)` / `withdraw_all()` - any delegator
- Attacker controls: the withdrawal amount, the receiving account and its state, and the epoch in which it is called
- Exploit idea: Make `internal_withdraw` subtract from `last_total_balance` NEAR that never actually left the account, on a pool created moments earlier through the public `create_staking_pool`.
- Invariant to test: `last_total_balance == env::account_balance() + env::account_locked_balance()` at the end of every call.
- Expected Immunefi impact: Critical - protocol insolvency: recorded claims exceed the NEAR the contract actually holds.
- Fast validation: Assert the equality after a failed-transfer withdraw in sim.
