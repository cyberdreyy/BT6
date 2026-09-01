# Q0603: Debited balance with a failing transfer - chained in one receipt

## Question
Can an unprivileged attacker withdraw to an account state where the fire-and-forget `Promise::new(account_id).transfer(amount)` fails, while `account.unstaked` and `last_total_balance` were already decremented with no callback to restore them, in a single transaction where the preceding action already moved `total_staked_balance`, breaking the invariant that NEAR that leaves the pool for an account equals the `unstaked` that account was debited, and leading to direct theft of delegator NEAR from the staking pool?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_withdraw`
- Entrypoint: `withdraw(amount)` / `withdraw_all()` - any delegator
- Attacker controls: the withdrawal amount, the receiving account and its state, and the epoch in which it is called
- Exploit idea: Withdraw to an account state where the fire-and-forget `Promise::new(account_id).transfer(amount)` fails, while `account.unstaked` and `last_total_balance` were already decremented with no callback to restore them, in a single transaction where the preceding action already moved `total_staked_balance`.
- Invariant to test: NEAR that leaves the pool for an account equals the `unstaked` that account was debited.
- Expected Immunefi impact: Critical - direct theft of delegator NEAR from the staking pool.
- Fast validation: Sim a withdraw whose transfer fails and assert either delivery or restoration of the balance.
