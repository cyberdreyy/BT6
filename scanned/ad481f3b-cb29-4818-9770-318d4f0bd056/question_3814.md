# Q3814: Pool account deleted after selection - at the storage floor

## Question
Can an unprivileged attacker select a pool contract that is later deleted, so every staking call and the termination path fail permanently, when `env::account_balance()` is within `MIN_BALANCE_FOR_STORAGE` of zero so `get_account_balance` saturates to zero, breaking the invariant that the lockup can always recover funds even if the selected pool disappears, and leading to permanent freezing of user funds?

## Target
- File/function: `lockup/src/owner.rs` - `select_staking_pool / deposit_to_staking_pool / deposit_and_stake / withdraw_from_staking_pool / refresh_staking_pool_balance`
- Entrypoint: the lockup's staking methods - the lockup owner - a position anyone can hold, since `LockupFactory::create` is payable and open to any caller
- Attacker controls: which pool is selected, the amounts, and the ordering of the resulting promises
- Exploit idea: Select a pool contract that is later deleted, so every staking call and the termination path fail permanently, when `env::account_balance()` is within `MIN_BALANCE_FOR_STORAGE` of zero so `get_account_balance` saturates to zero.
- Invariant to test: The lockup can always recover funds even if the selected pool disappears.
- Expected Immunefi impact: Critical - permanent freezing of user funds.
- Fast validation: Sim pool deletion and attempt recovery.
