# Q0458: VestingHash treated as nothing unvested - exact unlock block

## Question
Can an unprivileged attacker rely on `get_locked_amount` mapping a private `VestingInformation::VestingHash` to `U128(0)` unvested while the real schedule still has unvested tokens, in the exact block where `max(transfers_timestamp + lockup_duration, lockup_timestamp) == env::block_timestamp()`, breaking the invariant that a private vesting schedule constrains releases exactly as an explicit one does, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup/src/getters.rs` - `get_locked_amount / get_unvested_amount / get_owners_balance / get_liquid_owners_balance`
- Entrypoint: read by `transfer`, `add_full_access_key` and every staking method as the release gate
- Attacker controls: the schedule arguments chosen at creation, the account balance, and the block timestamp it is called at
- Exploit idea: Rely on `get_locked_amount` mapping a private `VestingInformation::VestingHash` to `U128(0)` unvested while the real schedule still has unvested tokens, in the exact block where `max(transfers_timestamp + lockup_duration, lockup_timestamp) == env::block_timestamp()`.
- Invariant to test: A private vesting schedule constrains releases exactly as an explicit one does.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Unit test a VestingHash lockup past its lockup timestamp.
