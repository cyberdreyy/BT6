# Q1628: Double settlement across withdraw and withdraw_all - last block of an epoch

## Question
Can an unprivileged attacker call `withdraw(amount)` and `withdraw_all()` in the same block so both settle against the same stored `unstaked`, in the last block of an epoch so the state change lands before the reward is distributed, breaking the invariant that the total NEAR delivered for one `unstaked` balance equals that balance exactly once, and leading to direct theft of delegator NEAR from the staking pool?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_withdraw`
- Entrypoint: `withdraw(amount)` / `withdraw_all()` - any delegator
- Attacker controls: the withdrawal amount, the receiving account and its state, and the epoch in which it is called
- Exploit idea: Call `withdraw(amount)` and `withdraw_all()` in the same block so both settle against the same stored `unstaked`, in the last block of an epoch so the state change lands before the reward is distributed.
- Invariant to test: The total NEAR delivered for one `unstaked` balance equals that balance exactly once.
- Expected Immunefi impact: Critical - direct theft of delegator NEAR from the staking pool.
- Fast validation: Sim both calls in one block and sum the transfers.
