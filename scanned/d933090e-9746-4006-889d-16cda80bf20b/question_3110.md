# Q3110: Deposit amount above the account balance check - after a donation

## Question
Can an unprivileged attacker pass an `amount` that passes `get_account_balance().0 >= amount.0` but leaves nothing for the storage reserve or for a pending obligation, after an outside account sent extra NEAR to the lockup with a bare `Transfer`, inflating `env::account_balance()`, breaking the invariant that amounts sent to a pool never encroach on the storage reserve or a pending termination, and leading to permanent freezing of user funds?

## Target
- File/function: `lockup/src/owner.rs` - `select_staking_pool / deposit_to_staking_pool / deposit_and_stake / withdraw_from_staking_pool / refresh_staking_pool_balance`
- Entrypoint: the lockup's staking methods - the lockup owner - a position anyone can hold, since `LockupFactory::create` is payable and open to any caller
- Attacker controls: which pool is selected, the amounts, and the ordering of the resulting promises
- Exploit idea: Pass an `amount` that passes `get_account_balance().0 >= amount.0` but leaves nothing for the storage reserve or for a pending obligation, after an outside account sent extra NEAR to the lockup with a bare `Transfer`, inflating `env::account_balance()`.
- Invariant to test: Amounts sent to a pool never encroach on the storage reserve or a pending termination.
- Expected Immunefi impact: Critical - permanent freezing of user funds.
- Fast validation: Sim a maximal deposit and inspect the remaining balance.
