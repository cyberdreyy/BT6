# Q3062: Deposit_amount inflated via refresh - after a donation

## Question
Can an unprivileged attacker call `refresh_staking_pool_balance` against a pool reporting an inflated total so `on_get_account_total_balance` overwrites `deposit_amount` with it, after an outside account sent extra NEAR to the lockup with a bare `Transfer`, inflating `env::account_balance()`, breaking the invariant that `deposit_amount` never exceeds what the pool can actually pay back, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup/src/owner.rs` - `select_staking_pool / deposit_to_staking_pool / deposit_and_stake / withdraw_from_staking_pool / refresh_staking_pool_balance`
- Entrypoint: the lockup's staking methods - the lockup owner - a position anyone can hold, since `LockupFactory::create` is payable and open to any caller
- Attacker controls: which pool is selected, the amounts, and the ordering of the resulting promises
- Exploit idea: Call `refresh_staking_pool_balance` against a pool reporting an inflated total so `on_get_account_total_balance` overwrites `deposit_amount` with it, after an outside account sent extra NEAR to the lockup with a bare `Transfer`, inflating `env::account_balance()`.
- Invariant to test: `deposit_amount` never exceeds what the pool can actually pay back.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Sim an inflated report then check transferable balance.
