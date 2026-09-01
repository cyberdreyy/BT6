# Q1458: Terminated state reports zero unvested - release_duration = 1ns

## Question
Can an unprivileged attacker use the `VestingInformation::Terminating` branch, which returns the frozen `unvested_amount` regardless of time, to release tokens the live schedule still locks, on a lockup created with `release_duration = Some(1)`, collapsing the U256 ratio into one nanosecond, breaking the invariant that termination never lowers the locked amount below the live schedule, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup/src/getters.rs` - `get_locked_amount / get_unvested_amount / get_owners_balance / get_liquid_owners_balance`
- Entrypoint: read by `transfer`, `add_full_access_key` and every staking method as the release gate
- Attacker controls: the schedule arguments chosen at creation, the account balance, and the block timestamp it is called at
- Exploit idea: Use the `VestingInformation::Terminating` branch, which returns the frozen `unvested_amount` regardless of time, to release tokens the live schedule still locks, on a lockup created with `release_duration = Some(1)`, collapsing the U256 ratio into one nanosecond.
- Invariant to test: Termination never lowers the locked amount below the live schedule.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Compare locked before and after a termination in sim.
