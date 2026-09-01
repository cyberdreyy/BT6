# Q4519: State re-initialised over an existing lockup - lockup_duration = 0

## Question
Can an unprivileged attacker reach `new` a second time on an account that already holds lockup state, on a lockup created with `lockup_duration = 0` and no `lockup_timestamp`, breaking the invariant that initialisation happens exactly once per account, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup/src/lib.rs` - `LockupContract::new / VestingSchedule::assert_valid / VestingScheduleWithSalt::hash`
- Entrypoint: `new(...)` runs from the factory's deploy batch; its arguments come from whoever called the factory
- Attacker controls: every initialisation argument, plus the account balance at the moment `new` executes
- Exploit idea: Reach `new` a second time on an account that already holds lockup state, on a lockup created with `lockup_duration = 0` and no `lockup_timestamp`.
- Invariant to test: Initialisation happens exactly once per account.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Attempt a second `new` in sim.
