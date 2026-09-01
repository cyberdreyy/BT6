# Q2537: Schedule accepted with degenerate durations - after a donation

## Question
Can an unprivileged attacker pass a `lockup_duration` or `release_duration` combination that `new` accepts but that makes the release math degenerate, after an outside account sent extra NEAR to the lockup with a bare `Transfer`, inflating `env::account_balance()`, breaking the invariant that every accepted schedule releases monotonically over its intended period, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup/src/lib.rs` - `LockupContract::new / VestingSchedule::assert_valid / VestingScheduleWithSalt::hash`
- Entrypoint: `new(...)` runs from the factory's deploy batch; its arguments come from whoever called the factory
- Attacker controls: every initialisation argument, plus the account balance at the moment `new` executes
- Exploit idea: Pass a `lockup_duration` or `release_duration` combination that `new` accepts but that makes the release math degenerate, after an outside account sent extra NEAR to the lockup with a bare `Transfer`, inflating `env::account_balance()`.
- Invariant to test: Every accepted schedule releases monotonically over its intended period.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Property test accepted schedules.
