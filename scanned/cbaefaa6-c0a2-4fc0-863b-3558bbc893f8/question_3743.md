# Q3743: Termination_withdrawn_tokens subtracted from the wrong side - after a donation

## Question
Can an unprivileged attacker use the `saturating_sub(self.lockup_information.termination_withdrawn_tokens)` inside the unreleased branch to erase locked tokens that were never withdrawn, after an outside account sent extra NEAR to the lockup with a bare `Transfer`, inflating `env::account_balance()`, breaking the invariant that subtracting withdrawn tokens never reduces locked below the vesting constraint, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup/src/getters.rs` - `get_locked_amount / get_unvested_amount / get_owners_balance / get_liquid_owners_balance`
- Entrypoint: read by `transfer`, `add_full_access_key` and every staking method as the release gate
- Attacker controls: the schedule arguments chosen at creation, the account balance, and the block timestamp it is called at
- Exploit idea: Use the `saturating_sub(self.lockup_information.termination_withdrawn_tokens)` inside the unreleased branch to erase locked tokens that were never withdrawn, after an outside account sent extra NEAR to the lockup with a bare `Transfer`, inflating `env::account_balance()`.
- Invariant to test: Subtracting withdrawn tokens never reduces locked below the vesting constraint.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Unit test with a non-zero `termination_withdrawn_tokens`.
