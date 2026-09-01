# Q5814: Release ratio rounds in the owner's favour - lockup_duration = 0

## Question
Can an unprivileged attacker exploit the `U256::from(lockup_amount) * time_left / release_duration` truncation in `get_locked_amount` so the reported locked amount is lower than the schedule implies, on a lockup created with `lockup_duration = 0` and no `lockup_timestamp`, breaking the invariant that `get_locked_amount()` is never below the exact rational value of the remaining schedule, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup/src/getters.rs` - `get_locked_amount / get_unvested_amount / get_owners_balance / get_liquid_owners_balance`
- Entrypoint: read by `transfer`, `add_full_access_key` and every staking method as the release gate
- Attacker controls: the schedule arguments chosen at creation, the account balance, and the block timestamp it is called at
- Exploit idea: Exploit the `U256::from(lockup_amount) * time_left / release_duration` truncation in `get_locked_amount` so the reported locked amount is lower than the schedule implies, on a lockup created with `lockup_duration = 0` and no `lockup_timestamp`.
- Invariant to test: `get_locked_amount()` is never below the exact rational value of the remaining schedule.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Unit test the ratio against exact arithmetic over many timestamps.
