# Q3532: Withdraw immediately after row recreation - first delegator

## Question
Can an unprivileged attacker delete the row via `internal_save_account`, recreate it with a deposit, and withdraw against the reset `unstaked_available_epoch_height`, as the first delegator, while `total_stake_shares` is still only the seeded `STAKE_SHARE_PRICE_GUARANTEE_FUND`, breaking the invariant that the unlock deadline survives row deletion and recreation, and leading to direct theft of delegator NEAR from the staking pool?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_withdraw`
- Entrypoint: `withdraw(amount)` / `withdraw_all()` - any delegator
- Attacker controls: the withdrawal amount, the receiving account and its state, and the epoch in which it is called
- Exploit idea: Delete the row via `internal_save_account`, recreate it with a deposit, and withdraw against the reset `unstaked_available_epoch_height`, as the first delegator, while `total_stake_shares` is still only the seeded `STAKE_SHARE_PRICE_GUARANTEE_FUND`.
- Invariant to test: The unlock deadline survives row deletion and recreation.
- Expected Immunefi impact: Critical - direct theft of delegator NEAR from the staking pool.
- Fast validation: Sim delete/recreate and assert `can_withdraw` is still false.
