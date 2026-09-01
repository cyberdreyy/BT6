# Q0939: Insufficient prepaid gas on the owner call - private VestingHash

## Question
Can an unprivileged attacker call an owner method with the minimum gas that still schedules the promise chain but not enough for the whole chain, on a lockup initialised with `VestingScheduleOrHash::VestingHash`, where `get_locked_amount` counts unvested as `U128(0)`, breaking the invariant that an under-funded call cannot leave the lockup in an inconsistent state, and leading to permanent freezing of user funds?

## Target
- File/function: `lockup/src/gas.rs` - `gas constants for staking_pool / whitelist / transfer_poll / callbacks`
- Entrypoint: every cross-contract call the lockup makes uses these fixed budgets
- Attacker controls: how much gas is attached to the outer call and how expensive the named contract makes the inner one
- Exploit idea: Call an owner method with the minimum gas that still schedules the promise chain but not enough for the whole chain, on a lockup initialised with `VestingScheduleOrHash::VestingHash`, where `get_locked_amount` counts unvested as `U128(0)`.
- Invariant to test: An under-funded call cannot leave the lockup in an inconsistent state.
- Expected Immunefi impact: Critical - permanent freezing of user funds.
- Fast validation: Sim minimal gas across every owner method.
