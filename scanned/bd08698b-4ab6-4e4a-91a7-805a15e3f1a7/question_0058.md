# Q0058: Termination_withdrawn_tokens subtracted from the wrong side - before unlock

## Question
Can an unprivileged attacker use the `saturating_sub(self.lockup_information.termination_withdrawn_tokens)` inside the unreleased branch to erase locked tokens that were never withdrawn, before `lockup_timestamp` passes, while `get_locked_amount` still returns the full `lockup_amount`, breaking the invariant that subtracting withdrawn tokens never reduces locked below the vesting constraint, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup/src/getters.rs` - `get_locked_amount / get_unvested_amount / get_owners_balance / get_liquid_owners_balance`
- Entrypoint: read by `transfer`, `add_full_access_key` and every staking method as the release gate
- Attacker controls: the schedule arguments chosen at creation, the account balance, and the block timestamp it is called at
- Exploit idea: Use the `saturating_sub(self.lockup_information.termination_withdrawn_tokens)` inside the unreleased branch to erase locked tokens that were never withdrawn, before `lockup_timestamp` passes, while `get_locked_amount` still returns the full `lockup_amount`.
- Invariant to test: Subtracting withdrawn tokens never reduces locked below the vesting constraint.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Unit test with a non-zero `termination_withdrawn_tokens`.
