# Q4763: Dust withdraw loop against last_total_balance - across an epoch skip

## Question
Can an unprivileged attacker repeat 1-yocto withdrawals so accumulated rounding in `last_total_balance` diverges from the real balance, after deliberately letting several epochs pass with no `ping` at all, breaking the invariant that `last_total_balance` tracks the real balance exactly, with no accumulating drift, and leading to protocol insolvency: recorded claims exceed the NEAR the contract actually holds?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_withdraw`
- Entrypoint: `withdraw(amount)` / `withdraw_all()` - any delegator
- Attacker controls: the withdrawal amount, the receiving account and its state, and the epoch in which it is called
- Exploit idea: Repeat 1-yocto withdrawals so accumulated rounding in `last_total_balance` diverges from the real balance, after deliberately letting several epochs pass with no `ping` at all.
- Invariant to test: `last_total_balance` tracks the real balance exactly, with no accumulating drift.
- Expected Immunefi impact: Critical - protocol insolvency: recorded claims exceed the NEAR the contract actually holds.
- Fast validation: Loop in sim and assert the equality each iteration.
