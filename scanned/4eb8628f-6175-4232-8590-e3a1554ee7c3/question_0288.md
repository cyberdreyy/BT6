# Q0288: Schedule accepted with degenerate durations - exact unlock block

## Question
Can an unprivileged attacker pass a `lockup_duration` or `release_duration` combination that `new` accepts but that makes the release math degenerate, in the exact block where `max(transfers_timestamp + lockup_duration, lockup_timestamp) == env::block_timestamp()`, breaking the invariant that every accepted schedule releases monotonically over its intended period, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup/src/lib.rs` - `LockupContract::new / VestingSchedule::assert_valid / VestingScheduleWithSalt::hash`
- Entrypoint: `new(...)` runs from the factory's deploy batch; its arguments come from whoever called the factory
- Attacker controls: every initialisation argument, plus the account balance at the moment `new` executes
- Exploit idea: Pass a `lockup_duration` or `release_duration` combination that `new` accepts but that makes the release math degenerate, in the exact block where `max(transfers_timestamp + lockup_duration, lockup_timestamp) == env::block_timestamp()`.
- Invariant to test: Every accepted schedule releases monotonically over its intended period.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Property test accepted schedules.
