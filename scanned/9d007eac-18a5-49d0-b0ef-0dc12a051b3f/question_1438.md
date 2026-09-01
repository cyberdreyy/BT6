# Q1438: Hash preimage ambiguity in the salted schedule - at the cliff

## Question
Can an unprivileged attacker exploit the borsh encoding of `VestingScheduleWithSalt` so two different schedules hash to the same commitment, at the exact `cliff_timestamp` nanosecond of the vesting schedule, breaking the invariant that the commitment binds exactly one schedule, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup/src/lib.rs` - `LockupContract::new / VestingSchedule::assert_valid / VestingScheduleWithSalt::hash`
- Entrypoint: `new(...)` runs from the factory's deploy batch; its arguments come from whoever called the factory
- Attacker controls: every initialisation argument, plus the account balance at the moment `new` executes
- Exploit idea: Exploit the borsh encoding of `VestingScheduleWithSalt` so two different schedules hash to the same commitment, at the exact `cliff_timestamp` nanosecond of the vesting schedule.
- Invariant to test: The commitment binds exactly one schedule.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Unit test the hash over crafted structures.
