# Q3653: Unselect with a hidden residual deposit - at the storage floor

## Question
Can an unprivileged attacker satisfy `unselect_staking_pool`'s `deposit_amount == 0` assertion while NEAR is still sitting in the pool, then select another pool and double count, when `env::account_balance()` is within `MIN_BALANCE_FOR_STORAGE` of zero so `get_account_balance` saturates to zero, breaking the invariant that unselecting is only possible when the lockup has no claim left at the pool, and leading to protocol accounting value diverges from reality and another party settles on it?

## Target
- File/function: `lockup/src/owner.rs` - `select_staking_pool / deposit_to_staking_pool / deposit_and_stake / withdraw_from_staking_pool / refresh_staking_pool_balance`
- Entrypoint: the lockup's staking methods - the lockup owner - a position anyone can hold, since `LockupFactory::create` is payable and open to any caller
- Attacker controls: which pool is selected, the amounts, and the ordering of the resulting promises
- Exploit idea: Satisfy `unselect_staking_pool`'s `deposit_amount == 0` assertion while NEAR is still sitting in the pool, then select another pool and double count, when `env::account_balance()` is within `MIN_BALANCE_FOR_STORAGE` of zero so `get_account_balance` saturates to zero.
- Invariant to test: Unselecting is only possible when the lockup has no claim left at the pool.
- Expected Immunefi impact: High - protocol accounting value diverges from reality and another party settles on it.
- Fast validation: Sim unselect with residual balance and reconcile.
