# Q1914: Outer call succeeds, callback fails - no vesting schedule

## Question
Can an unprivileged attacker arrange for the value-moving promise to succeed while its state-updating callback fails on gas, on a lockup created with `vesting_schedule = None`, where `VestingInformation::None` short-circuits the unvested branch, breaking the invariant that value movement and its bookkeeping succeed or fail together, and leading to protocol accounting value diverges from reality and another party settles on it?

## Target
- File/function: `lockup/src/gas.rs` - `gas constants for staking_pool / whitelist / transfer_poll / callbacks`
- Entrypoint: every cross-contract call the lockup makes uses these fixed budgets
- Attacker controls: how much gas is attached to the outer call and how expensive the named contract makes the inner one
- Exploit idea: Arrange for the value-moving promise to succeed while its state-updating callback fails on gas, on a lockup created with `vesting_schedule = None`, where `VestingInformation::None` short-circuits the unvested branch.
- Invariant to test: Value movement and its bookkeeping succeed or fail together.
- Expected Immunefi impact: High - protocol accounting value diverges from reality and another party settles on it.
- Fast validation: Sim the split outcome and reconcile.
