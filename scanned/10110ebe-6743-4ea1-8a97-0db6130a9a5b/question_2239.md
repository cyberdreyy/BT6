# Q2239: Nested promise depth from a hostile pool - just after unselect

## Question
Can an unprivileged attacker have the named pool return a promise chain that pushes the callback past the receipt's remaining gas, in the receipt right after `unselect_staking_pool` cleared the staking information, breaking the invariant that the lockup's callbacks are independent of the callee's promise structure, and leading to protocol accounting value diverges from reality and another party settles on it?

## Target
- File/function: `lockup/src/gas.rs` - `gas constants for staking_pool / whitelist / transfer_poll / callbacks`
- Entrypoint: every cross-contract call the lockup makes uses these fixed budgets
- Attacker controls: how much gas is attached to the outer call and how expensive the named contract makes the inner one
- Exploit idea: Have the named pool return a promise chain that pushes the callback past the receipt's remaining gas, in the receipt right after `unselect_staking_pool` cleared the staking information.
- Invariant to test: The lockup's callbacks are independent of the callee's promise structure.
- Expected Immunefi impact: High - protocol accounting value diverges from reality and another party settles on it.
- Fast validation: Sim a pool returning a chained promise.
