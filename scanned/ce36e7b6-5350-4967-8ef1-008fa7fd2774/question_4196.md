# Q4196: Double settlement across withdraw and withdraw_all - paused pool

## Question
Can an unprivileged attacker call `withdraw(amount)` and `withdraw_all()` in the same block so both settle against the same stored `unstaked`, while `paused == true`, so `internal_restake` returns early and nothing is re-staked, breaking the invariant that the total NEAR delivered for one `unstaked` balance equals that balance exactly once, and leading to direct theft of delegator NEAR from the staking pool?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_withdraw`
- Entrypoint: `withdraw(amount)` / `withdraw_all()` - any delegator
- Attacker controls: the withdrawal amount, the receiving account and its state, and the epoch in which it is called
- Exploit idea: Call `withdraw(amount)` and `withdraw_all()` in the same block so both settle against the same stored `unstaked`, while `paused == true`, so `internal_restake` returns early and nothing is re-staked.
- Invariant to test: The total NEAR delivered for one `unstaked` balance equals that balance exactly once.
- Expected Immunefi impact: Critical - direct theft of delegator NEAR from the staking pool.
- Fast validation: Sim both calls in one block and sum the transfers.
