# Q0733: Locked amount changes without a state change - exact unlock block

## Question
Can an unprivileged attacker find two calls at the same block timestamp that return different locked amounts because of intervening balance movements, in the exact block where `max(transfers_timestamp + lockup_duration, lockup_timestamp) == env::block_timestamp()`, breaking the invariant that the locked amount depends only on the schedule and the timestamp, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup/src/getters.rs` - `get_locked_amount / get_unvested_amount / get_owners_balance / get_liquid_owners_balance`
- Entrypoint: read by `transfer`, `add_full_access_key` and every staking method as the release gate
- Attacker controls: the schedule arguments chosen at creation, the account balance, and the block timestamp it is called at
- Exploit idea: Find two calls at the same block timestamp that return different locked amounts because of intervening balance movements, in the exact block where `max(transfers_timestamp + lockup_duration, lockup_timestamp) == env::block_timestamp()`.
- Invariant to test: The locked amount depends only on the schedule and the timestamp.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Assert determinism across balance changes in a unit test.
