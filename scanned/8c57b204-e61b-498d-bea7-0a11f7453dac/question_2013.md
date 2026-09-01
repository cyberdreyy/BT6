# Q2013: Lockup_amount captured from a manipulated balance - mid-termination

## Question
Can an unprivileged attacker influence `env::account_balance()` at the instant `new` runs, since `lockup_information.lockup_amount` is set from it, so the schedule locks a different amount than the grant, while `vesting_information` is `Terminating` and `termination_withdrawn_tokens` is non-zero, breaking the invariant that `lockup_amount` equals the NEAR the grant actually funded, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup/src/lib.rs` - `LockupContract::new / VestingSchedule::assert_valid / VestingScheduleWithSalt::hash`
- Entrypoint: `new(...)` runs from the factory's deploy batch; its arguments come from whoever called the factory
- Attacker controls: every initialisation argument, plus the account balance at the moment `new` executes
- Exploit idea: Influence `env::account_balance()` at the instant `new` runs, since `lockup_information.lockup_amount` is set from it, so the schedule locks a different amount than the grant, while `vesting_information` is `Terminating` and `termination_withdrawn_tokens` is non-zero.
- Invariant to test: `lockup_amount` equals the NEAR the grant actually funded.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Sim a pre-funded account id then create the lockup and check the field.
