# Q3518: Vesting hash committed without a foundation - balance seeded before init

## Question
Can an unprivileged attacker reach a state where a vesting commitment exists but no usable `foundation_account_id` can act on it, on a lockup whose account already held NEAR when `new` ran, so `lockup_amount` came from a balance an outsider influenced, breaking the invariant that a vesting schedule always has an account able to terminate it, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup/src/lib.rs` - `LockupContract::new / VestingSchedule::assert_valid / VestingScheduleWithSalt::hash`
- Entrypoint: `new(...)` runs from the factory's deploy batch; its arguments come from whoever called the factory
- Attacker controls: every initialisation argument, plus the account balance at the moment `new` executes
- Exploit idea: Reach a state where a vesting commitment exists but no usable `foundation_account_id` can act on it, on a lockup whose account already held NEAR when `new` ran, so `lockup_amount` came from a balance an outsider influenced.
- Invariant to test: A vesting schedule always has an account able to terminate it.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Unit test the argument combinations `new` accepts.
