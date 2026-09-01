# Q4367: Max() of unreleased and unvested picks the wrong branch - at the storage floor

## Question
Can an unprivileged attacker arrange a schedule where `max(unreleased - termination_withdrawn_tokens, unvested)` returns a value smaller than both constraints require, when `env::account_balance()` is within `MIN_BALANCE_FOR_STORAGE` of zero so `get_account_balance` saturates to zero, breaking the invariant that locked equals the maximum of the release constraint and the vesting constraint at all times, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup/src/getters.rs` - `get_locked_amount / get_unvested_amount / get_owners_balance / get_liquid_owners_balance`
- Entrypoint: read by `transfer`, `add_full_access_key` and every staking method as the release gate
- Attacker controls: the schedule arguments chosen at creation, the account balance, and the block timestamp it is called at
- Exploit idea: Arrange a schedule where `max(unreleased - termination_withdrawn_tokens, unvested)` returns a value smaller than both constraints require, when `env::account_balance()` is within `MIN_BALANCE_FOR_STORAGE` of zero so `get_account_balance` saturates to zero.
- Invariant to test: Locked equals the maximum of the release constraint and the vesting constraint at all times.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Property test both constraints against the returned value.
