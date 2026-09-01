# Q3633: Hash preimage ambiguity in the salted schedule - balance seeded before init

## Question
Can an unprivileged attacker exploit the borsh encoding of `VestingScheduleWithSalt` so two different schedules hash to the same commitment, on a lockup whose account already held NEAR when `new` ran, so `lockup_amount` came from a balance an outsider influenced, breaking the invariant that the commitment binds exactly one schedule, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup/src/lib.rs` - `LockupContract::new / VestingSchedule::assert_valid / VestingScheduleWithSalt::hash`
- Entrypoint: `new(...)` runs from the factory's deploy batch; its arguments come from whoever called the factory
- Attacker controls: every initialisation argument, plus the account balance at the moment `new` executes
- Exploit idea: Exploit the borsh encoding of `VestingScheduleWithSalt` so two different schedules hash to the same commitment, on a lockup whose account already held NEAR when `new` ran, so `lockup_amount` came from a balance an outsider influenced.
- Invariant to test: The commitment binds exactly one schedule.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Unit test the hash over crafted structures.
