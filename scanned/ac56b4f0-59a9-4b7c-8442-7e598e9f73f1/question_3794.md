# Q3794: Poll account id chosen by the creator - no vesting schedule

## Question
Can an unprivileged attacker initialise with a `transfer_poll_account_id` under the attacker's control, so transfers can be enabled at will later, on a lockup created with `vesting_schedule = None`, where `VestingInformation::None` short-circuits the unvested branch, breaking the invariant that the poll account is the canonical transfer poll, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup/src/lib.rs` - `LockupContract::new / VestingSchedule::assert_valid / VestingScheduleWithSalt::hash`
- Entrypoint: `new(...)` runs from the factory's deploy batch; its arguments come from whoever called the factory
- Attacker controls: every initialisation argument, plus the account balance at the moment `new` executes
- Exploit idea: Initialise with a `transfer_poll_account_id` under the attacker's control, so transfers can be enabled at will later, on a lockup created with `vesting_schedule = None`, where `VestingInformation::None` short-circuits the unvested branch.
- Invariant to test: The poll account is the canonical transfer poll.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Sim a hostile poll id and enable transfers.
