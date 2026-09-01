# Q4154: Withdraw_all on a row that does not exist - paused pool

## Question
Can an unprivileged attacker call `withdraw_all` from an account whose row was deleted so `internal_get_account` yields defaults, and the amount assertion is evaluated against zeros, while `paused == true`, so `internal_restake` returns early and nothing is re-staked, breaking the invariant that `withdraw_all` moves exactly the caller's stored `unstaked` balance, and leading to direct theft of delegator NEAR from the staking pool?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_withdraw`
- Entrypoint: `withdraw(amount)` / `withdraw_all()` - any delegator
- Attacker controls: the withdrawal amount, the receiving account and its state, and the epoch in which it is called
- Exploit idea: Call `withdraw_all` from an account whose row was deleted so `internal_get_account` yields defaults, and the amount assertion is evaluated against zeros, while `paused == true`, so `internal_restake` returns early and nothing is re-staked.
- Invariant to test: `withdraw_all` moves exactly the caller's stored `unstaked` balance.
- Expected Immunefi impact: Critical - direct theft of delegator NEAR from the staking pool.
- Fast validation: Unit test withdraw_all from an unknown account.
