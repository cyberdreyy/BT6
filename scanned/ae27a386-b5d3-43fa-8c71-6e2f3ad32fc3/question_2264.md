# Q2264: Callback starved by a slow named contract - lockup_duration = 0

## Question
Can an unprivileged attacker select a pool or poll contract whose method consumes so much of the fixed budget that the scheduled callback cannot complete, on a lockup created with `lockup_duration = 0` and no `lockup_timestamp`, breaking the invariant that the callback always has enough gas to finish once the outer call succeeded, and leading to permanent freezing of user funds?

## Target
- File/function: `lockup/src/gas.rs` - `gas constants for staking_pool / whitelist / transfer_poll / callbacks`
- Entrypoint: every cross-contract call the lockup makes uses these fixed budgets
- Attacker controls: how much gas is attached to the outer call and how expensive the named contract makes the inner one
- Exploit idea: Select a pool or poll contract whose method consumes so much of the fixed budget that the scheduled callback cannot complete, on a lockup created with `lockup_duration = 0` and no `lockup_timestamp`.
- Invariant to test: The callback always has enough gas to finish once the outer call succeeded.
- Expected Immunefi impact: Critical - permanent freezing of user funds.
- Fast validation: Sim a gas-heavy pool and check for a stuck `Busy` status.
