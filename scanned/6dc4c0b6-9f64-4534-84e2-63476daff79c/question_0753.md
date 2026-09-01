# Q0753: Withdrawing to a to-be-deleted account - chained in one receipt

## Question
Can an unprivileged attacker withdraw to an account the attacker deletes in the same block, so the transfer refunds into the pool while the debit stands, in a single transaction where the preceding action already moved `total_staked_balance`, breaking the invariant that a refunded transfer restores the debited `unstaked` balance, and leading to protocol insolvency: recorded claims exceed the NEAR the contract actually holds?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_withdraw`
- Entrypoint: `withdraw(amount)` / `withdraw_all()` - any delegator
- Attacker controls: the withdrawal amount, the receiving account and its state, and the epoch in which it is called
- Exploit idea: Withdraw to an account the attacker deletes in the same block, so the transfer refunds into the pool while the debit stands, in a single transaction where the preceding action already moved `total_staked_balance`.
- Invariant to test: A refunded transfer restores the debited `unstaked` balance.
- Expected Immunefi impact: Critical - protocol insolvency: recorded claims exceed the NEAR the contract actually holds.
- Fast validation: Sim account deletion mid-withdraw and reconcile totals.
