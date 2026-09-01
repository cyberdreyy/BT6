# Q3771: Transfers pre-enabled with a chosen timestamp - no vesting schedule

## Question
Can an unprivileged attacker initialise with `TransfersInformation::TransfersEnabled { transfers_timestamp }` set to a past timestamp so the lockup period is already over, on a lockup created with `vesting_schedule = None`, where `VestingInformation::None` short-circuits the unvested branch, breaking the invariant that the transfers timestamp reflects the real network event, not a caller-chosen value, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup/src/lib.rs` - `LockupContract::new / VestingSchedule::assert_valid / VestingScheduleWithSalt::hash`
- Entrypoint: `new(...)` runs from the factory's deploy batch; its arguments come from whoever called the factory
- Attacker controls: every initialisation argument, plus the account balance at the moment `new` executes
- Exploit idea: Initialise with `TransfersInformation::TransfersEnabled { transfers_timestamp }` set to a past timestamp so the lockup period is already over, on a lockup created with `vesting_schedule = None`, where `VestingInformation::None` short-circuits the unvested branch.
- Invariant to test: The transfers timestamp reflects the real network event, not a caller-chosen value.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Create a lockup with a past timestamp and check the locked amount.
