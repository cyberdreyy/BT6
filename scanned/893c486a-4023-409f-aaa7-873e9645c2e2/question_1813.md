# Q1813: Vesting hash committed without a foundation - private VestingHash

## Question
Can an unprivileged attacker reach a state where a vesting commitment exists but no usable `foundation_account_id` can act on it, on a lockup initialised with `VestingScheduleOrHash::VestingHash`, where `get_locked_amount` counts unvested as `U128(0)`, breaking the invariant that a vesting schedule always has an account able to terminate it, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup/src/lib.rs` - `LockupContract::new / VestingSchedule::assert_valid / VestingScheduleWithSalt::hash`
- Entrypoint: `new(...)` runs from the factory's deploy batch; its arguments come from whoever called the factory
- Attacker controls: every initialisation argument, plus the account balance at the moment `new` executes
- Exploit idea: Reach a state where a vesting commitment exists but no usable `foundation_account_id` can act on it, on a lockup initialised with `VestingScheduleOrHash::VestingHash`, where `get_locked_amount` counts unvested as `U128(0)`.
- Invariant to test: A vesting schedule always has an account able to terminate it.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Unit test the argument combinations `new` accepts.
