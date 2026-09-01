# Q5627: Terminated state reports zero unvested - no staking pool selected

## Question
Can an unprivileged attacker use the `VestingInformation::Terminating` branch, which returns the frozen `unvested_amount` regardless of time, to release tokens the live schedule still locks, while `staking_information` is `None`, so the deposit term drops out of the balance calculation, breaking the invariant that termination never lowers the locked amount below the live schedule, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup/src/getters.rs` - `get_locked_amount / get_unvested_amount / get_owners_balance / get_liquid_owners_balance`
- Entrypoint: read by `transfer`, `add_full_access_key` and every staking method as the release gate
- Attacker controls: the schedule arguments chosen at creation, the account balance, and the block timestamp it is called at
- Exploit idea: Use the `VestingInformation::Terminating` branch, which returns the frozen `unvested_amount` regardless of time, to release tokens the live schedule still locks, while `staking_information` is `None`, so the deposit term drops out of the balance calculation.
- Invariant to test: Termination never lowers the locked amount below the live schedule.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Compare locked before and after a termination in sim.
