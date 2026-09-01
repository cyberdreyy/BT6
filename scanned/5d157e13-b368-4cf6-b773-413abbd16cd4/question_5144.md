# Q5144: Release_duration zero divides or collapses - balance seeded before init

## Question
Can an unprivileged attacker create the lockup with `release_duration = Some(0)` so the ratio path degenerates and everything is released at the unlock timestamp, on a lockup whose account already held NEAR when `new` ran, so `lockup_amount` came from a balance an outsider influenced, breaking the invariant that a zero release duration cannot release more than the schedule intends at any instant, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup/src/getters.rs` - `get_locked_amount / get_unvested_amount / get_owners_balance / get_liquid_owners_balance`
- Entrypoint: read by `transfer`, `add_full_access_key` and every staking method as the release gate
- Attacker controls: the schedule arguments chosen at creation, the account balance, and the block timestamp it is called at
- Exploit idea: Create the lockup with `release_duration = Some(0)` so the ratio path degenerates and everything is released at the unlock timestamp, on a lockup whose account already held NEAR when `new` ran, so `lockup_amount` came from a balance an outsider influenced.
- Invariant to test: A zero release duration cannot release more than the schedule intends at any instant.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Unit test the zero and one-nanosecond cases.
