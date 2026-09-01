# Q4036: Whitelist account id chosen by the creator - no staking pool selected

## Question
Can an unprivileged attacker initialise with a `staking_pool_whitelist_account_id` the attacker controls so any pool can later be selected, while `staking_information` is `None`, so the deposit term drops out of the balance calculation, breaking the invariant that the whitelist is the canonical staking pool whitelist, and leading to user funds routed into an attacker-parameterised contract the protocol treats as trusted?

## Target
- File/function: `lockup/src/lib.rs` - `LockupContract::new / VestingSchedule::assert_valid / VestingScheduleWithSalt::hash`
- Entrypoint: `new(...)` runs from the factory's deploy batch; its arguments come from whoever called the factory
- Attacker controls: every initialisation argument, plus the account balance at the moment `new` executes
- Exploit idea: Initialise with a `staking_pool_whitelist_account_id` the attacker controls so any pool can later be selected, while `staking_information` is `None`, so the deposit term drops out of the balance calculation.
- Invariant to test: The whitelist is the canonical staking pool whitelist.
- Expected Immunefi impact: Critical - user funds routed into an attacker-parameterised contract the protocol treats as trusted.
- Fast validation: Sim a hostile whitelist id and select a pool.
