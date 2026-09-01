# Q4049: Withdraw larger than the account balance the pool holds - after a bare donation

## Question
Can an unprivileged attacker withdraw an amount the pool can only satisfy from other delegators' unstaked NEAR, after first sending NEAR straight to the pool account with a bare `Transfer` outside any method, breaking the invariant that one account's withdrawal never draws on another account's balance, and leading to direct theft of delegator NEAR from the staking pool?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_withdraw`
- Entrypoint: `withdraw(amount)` / `withdraw_all()` - any delegator
- Attacker controls: the withdrawal amount, the receiving account and its state, and the epoch in which it is called
- Exploit idea: Withdraw an amount the pool can only satisfy from other delegators' unstaked NEAR, after first sending NEAR straight to the pool account with a bare `Transfer` outside any method.
- Invariant to test: One account's withdrawal never draws on another account's balance.
- Expected Immunefi impact: Critical - direct theft of delegator NEAR from the staking pool.
- Fast validation: Sim two delegators and assert the second can still withdraw in full.
