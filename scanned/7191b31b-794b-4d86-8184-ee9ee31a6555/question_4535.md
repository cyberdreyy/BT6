# Q4535: Locked_vested subtraction underflow - at the storage floor

## Question
Can an unprivileged attacker call `get_locked_vested_amount` with a `vesting_schedule` argument that makes `get_locked_amount().0 - get_unvested_amount().0` underflow, when `env::account_balance()` is within `MIN_BALANCE_FOR_STORAGE` of zero so `get_account_balance` saturates to zero, breaking the invariant that `get_locked_vested_amount` never underflows for any caller-supplied schedule, and leading to protocol accounting value diverges from reality and another party settles on it?

## Target
- File/function: `lockup/src/getters.rs` - `get_locked_amount / get_unvested_amount / get_owners_balance / get_liquid_owners_balance`
- Entrypoint: read by `transfer`, `add_full_access_key` and every staking method as the release gate
- Attacker controls: the schedule arguments chosen at creation, the account balance, and the block timestamp it is called at
- Exploit idea: Call `get_locked_vested_amount` with a `vesting_schedule` argument that makes `get_locked_amount().0 - get_unvested_amount().0` underflow, when `env::account_balance()` is within `MIN_BALANCE_FOR_STORAGE` of zero so `get_account_balance` saturates to zero.
- Invariant to test: `get_locked_vested_amount` never underflows for any caller-supplied schedule.
- Expected Immunefi impact: High - protocol accounting value diverges from reality and another party settles on it.
- Fast validation: Unit test with adversarial schedule arguments.
