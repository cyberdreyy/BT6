# Q2353: Withdraw larger than the account balance the pool holds - amount = balance - 1

## Question
Can an unprivileged attacker withdraw an amount the pool can only satisfy from other delegators' unstaked NEAR, with `amount` one yoctoNEAR below the attacker's own recorded balance, breaking the invariant that one account's withdrawal never draws on another account's balance, and leading to direct theft of delegator NEAR from the staking pool?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_withdraw`
- Entrypoint: `withdraw(amount)` / `withdraw_all()` - any delegator
- Attacker controls: the withdrawal amount, the receiving account and its state, and the epoch in which it is called
- Exploit idea: Withdraw an amount the pool can only satisfy from other delegators' unstaked NEAR, with `amount` one yoctoNEAR below the attacker's own recorded balance.
- Invariant to test: One account's withdrawal never draws on another account's balance.
- Expected Immunefi impact: Critical - direct theft of delegator NEAR from the staking pool.
- Fast validation: Sim two delegators and assert the second can still withdraw in full.
