# Q2729: Release_duration without a lockup timestamp - after a donation

## Question
Can an unprivileged attacker combine `release_duration` with an absent `lockup_timestamp` so the unlock reference point is a value the creator controls, after an outside account sent extra NEAR to the lockup with a bare `Transfer`, inflating `env::account_balance()`, breaking the invariant that the unlock reference point comes from the grant, not the caller, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup/src/lib.rs` - `LockupContract::new / VestingSchedule::assert_valid / VestingScheduleWithSalt::hash`
- Entrypoint: `new(...)` runs from the factory's deploy batch; its arguments come from whoever called the factory
- Attacker controls: every initialisation argument, plus the account balance at the moment `new` executes
- Exploit idea: Combine `release_duration` with an absent `lockup_timestamp` so the unlock reference point is a value the creator controls, after an outside account sent extra NEAR to the lockup with a bare `Transfer`, inflating `env::account_balance()`.
- Invariant to test: The unlock reference point comes from the grant, not the caller.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Unit test the combination.
