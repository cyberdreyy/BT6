# Q5638: Locked amount changes without a state change - no staking pool selected

## Question
Can an unprivileged attacker find two calls at the same block timestamp that return different locked amounts because of intervening balance movements, while `staking_information` is `None`, so the deposit term drops out of the balance calculation, breaking the invariant that the locked amount depends only on the schedule and the timestamp, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup/src/getters.rs` - `get_locked_amount / get_unvested_amount / get_owners_balance / get_liquid_owners_balance`
- Entrypoint: read by `transfer`, `add_full_access_key` and every staking method as the release gate
- Attacker controls: the schedule arguments chosen at creation, the account balance, and the block timestamp it is called at
- Exploit idea: Find two calls at the same block timestamp that return different locked amounts because of intervening balance movements, while `staking_information` is `None`, so the deposit term drops out of the balance calculation.
- Invariant to test: The locked amount depends only on the schedule and the timestamp.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Assert determinism across balance changes in a unit test.
