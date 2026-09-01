# Q2671: Withdraw immediately after row recreation - u128::MAX

## Question
Can an unprivileged attacker delete the row via `internal_save_account`, recreate it with a deposit, and withdraw against the reset `unstaked_available_epoch_height`, with `amount = u128::MAX` so the U256 product dwarfs any real balance, breaking the invariant that the unlock deadline survives row deletion and recreation, and leading to direct theft of delegator NEAR from the staking pool?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_withdraw`
- Entrypoint: `withdraw(amount)` / `withdraw_all()` - any delegator
- Attacker controls: the withdrawal amount, the receiving account and its state, and the epoch in which it is called
- Exploit idea: Delete the row via `internal_save_account`, recreate it with a deposit, and withdraw against the reset `unstaked_available_epoch_height`, with `amount = u128::MAX` so the U256 product dwarfs any real balance.
- Invariant to test: The unlock deadline survives row deletion and recreation.
- Expected Immunefi impact: Critical - direct theft of delegator NEAR from the staking pool.
- Fast validation: Sim delete/recreate and assert `can_withdraw` is still false.
