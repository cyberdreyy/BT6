# Q5704: Saturating_add hides a lockup timestamp overflow - just after unselect

## Question
Can an unprivileged attacker choose `lockup_duration` so `transfers_timestamp.saturating_add(lockup_duration)` saturates and the comparison against `block_timestamp` flips, in the receipt right after `unselect_staking_pool` cleared the staking information, breaking the invariant that the unlock timestamp is the true sum of the schedule fields, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup/src/getters.rs` - `get_locked_amount / get_unvested_amount / get_owners_balance / get_liquid_owners_balance`
- Entrypoint: read by `transfer`, `add_full_access_key` and every staking method as the release gate
- Attacker controls: the schedule arguments chosen at creation, the account balance, and the block timestamp it is called at
- Exploit idea: Choose `lockup_duration` so `transfers_timestamp.saturating_add(lockup_duration)` saturates and the comparison against `block_timestamp` flips, in the receipt right after `unselect_staking_pool` cleared the staking information.
- Invariant to test: The unlock timestamp is the true sum of the schedule fields.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Unit test with near-`u64::MAX` durations.
