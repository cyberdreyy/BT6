# Q4729: State re-initialised over an existing lockup - with foundation absent

## Question
Can an unprivileged attacker reach `new` a second time on an account that already holds lockup state, on a lockup where `foundation_account_id` is `None` so the termination path can never run, breaking the invariant that initialisation happens exactly once per account, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup/src/lib.rs` - `LockupContract::new / VestingSchedule::assert_valid / VestingScheduleWithSalt::hash`
- Entrypoint: `new(...)` runs from the factory's deploy batch; its arguments come from whoever called the factory
- Attacker controls: every initialisation argument, plus the account balance at the moment `new` executes
- Exploit idea: Reach `new` a second time on an account that already holds lockup state, on a lockup where `foundation_account_id` is `None` so the termination path can never run.
- Invariant to test: Initialisation happens exactly once per account.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Attempt a second `new` in sim.
