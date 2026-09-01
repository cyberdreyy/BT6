# Q1433: Unvested computed from an unchecked schedule ordering - release_duration = 1ns

## Question
Can an unprivileged attacker supply schedule fields that pass `VestingSchedule::assert_valid` yet make `end_timestamp - start_timestamp` a value that skews the ratio, on a lockup created with `release_duration = Some(1)`, collapsing the U256 ratio into one nanosecond, breaking the invariant that the vesting ratio is exact for every schedule that passes validation, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup/src/getters.rs` - `get_locked_amount / get_unvested_amount / get_owners_balance / get_liquid_owners_balance`
- Entrypoint: read by `transfer`, `add_full_access_key` and every staking method as the release gate
- Attacker controls: the schedule arguments chosen at creation, the account balance, and the block timestamp it is called at
- Exploit idea: Supply schedule fields that pass `VestingSchedule::assert_valid` yet make `end_timestamp - start_timestamp` a value that skews the ratio, on a lockup created with `release_duration = Some(1)`, collapsing the U256 ratio into one nanosecond.
- Invariant to test: The vesting ratio is exact for every schedule that passes validation.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Property test valid schedules against exact arithmetic.
