# Q1233: Pre-transfers branch ignores release_duration - release_duration = 1ns

## Question
Can an unprivileged attacker use the fall-through `lockup_amount - termination_withdrawn_tokens` branch taken while transfers are disabled to reach a smaller locked value than the enabled branch would give, on a lockup created with `release_duration = Some(1)`, collapsing the U256 ratio into one nanosecond, breaking the invariant that enabling transfers never decreases the locked amount at the same timestamp, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup/src/getters.rs` - `get_locked_amount / get_unvested_amount / get_owners_balance / get_liquid_owners_balance`
- Entrypoint: read by `transfer`, `add_full_access_key` and every staking method as the release gate
- Attacker controls: the schedule arguments chosen at creation, the account balance, and the block timestamp it is called at
- Exploit idea: Use the fall-through `lockup_amount - termination_withdrawn_tokens` branch taken while transfers are disabled to reach a smaller locked value than the enabled branch would give, on a lockup created with `release_duration = Some(1)`, collapsing the U256 ratio into one nanosecond.
- Invariant to test: Enabling transfers never decreases the locked amount at the same timestamp.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Compare both branches at the same timestamp in a unit test.
