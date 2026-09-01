# Q1839: Termination chain gas exhaustion - balance seeded before init

## Question
Can an unprivileged attacker starve the termination chain so it stalls between two statuses, on a lockup whose account already held NEAR when `new` ran, so `lockup_amount` came from a balance an outsider influenced, breaking the invariant that the termination chain always progresses or fully reverts, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup/src/gas.rs` - `gas constants for staking_pool / whitelist / transfer_poll / callbacks`
- Entrypoint: every cross-contract call the lockup makes uses these fixed budgets
- Attacker controls: how much gas is attached to the outer call and how expensive the named contract makes the inner one
- Exploit idea: Starve the termination chain so it stalls between two statuses, on a lockup whose account already held NEAR when `new` ran, so `lockup_amount` came from a balance an outsider influenced.
- Invariant to test: The termination chain always progresses or fully reverts.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Sim minimal gas during termination.
