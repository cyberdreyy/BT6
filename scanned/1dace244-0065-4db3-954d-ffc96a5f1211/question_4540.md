# Q4540: Release_duration without a lockup timestamp - lockup_duration = 0

## Question
Can an unprivileged attacker combine `release_duration` with an absent `lockup_timestamp` so the unlock reference point is a value the creator controls, on a lockup created with `lockup_duration = 0` and no `lockup_timestamp`, breaking the invariant that the unlock reference point comes from the grant, not the caller, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup/src/lib.rs` - `LockupContract::new / VestingSchedule::assert_valid / VestingScheduleWithSalt::hash`
- Entrypoint: `new(...)` runs from the factory's deploy batch; its arguments come from whoever called the factory
- Attacker controls: every initialisation argument, plus the account balance at the moment `new` executes
- Exploit idea: Combine `release_duration` with an absent `lockup_timestamp` so the unlock reference point is a value the creator controls, on a lockup created with `lockup_duration = 0` and no `lockup_timestamp`.
- Invariant to test: The unlock reference point comes from the grant, not the caller.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Unit test the combination.
