# Q0703: Unlock gate evaluated against a reset epoch - chained in one receipt

## Question
Can an unprivileged attacker withdraw when `unstaked_available_epoch_height <= env::epoch_height()` holds for a reason other than four epochs having actually elapsed, in a single transaction where the preceding action already moved `total_staked_balance`, breaking the invariant that unstaked NEAR is only withdrawable `NUM_EPOCHS_TO_UNLOCK` epochs after the unstake that created it, and leading to direct theft of delegator NEAR from the staking pool?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_withdraw`
- Entrypoint: `withdraw(amount)` / `withdraw_all()` - any delegator
- Attacker controls: the withdrawal amount, the receiving account and its state, and the epoch in which it is called
- Exploit idea: Withdraw when `unstaked_available_epoch_height <= env::epoch_height()` holds for a reason other than four epochs having actually elapsed, in a single transaction where the preceding action already moved `total_staked_balance`.
- Invariant to test: Unstaked NEAR is only withdrawable `NUM_EPOCHS_TO_UNLOCK` epochs after the unstake that created it.
- Expected Immunefi impact: Critical - direct theft of delegator NEAR from the staking pool.
- Fast validation: Sim epochs and assert the gate at each boundary.
