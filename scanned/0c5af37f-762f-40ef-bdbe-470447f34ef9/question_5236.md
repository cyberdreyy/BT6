# Q5236: Gas starvation of the staking callback - lockup_duration = 0

## Question
Can an unprivileged attacker call a staking method with just enough gas that the scheduled callback runs out before clearing `Busy` or updating `deposit_amount`, on a lockup created with `lockup_duration = 0` and no `lockup_timestamp`, breaking the invariant that the callback either completes or leaves the state exactly as it was, and leading to permanent freezing of user funds?

## Target
- File/function: `lockup/src/owner.rs` - `select_staking_pool / deposit_to_staking_pool / deposit_and_stake / withdraw_from_staking_pool / refresh_staking_pool_balance`
- Entrypoint: the lockup's staking methods - the lockup owner - a position anyone can hold, since `LockupFactory::create` is payable and open to any caller
- Attacker controls: which pool is selected, the amounts, and the ordering of the resulting promises
- Exploit idea: Call a staking method with just enough gas that the scheduled callback runs out before clearing `Busy` or updating `deposit_amount`, on a lockup created with `lockup_duration = 0` and no `lockup_timestamp`.
- Invariant to test: The callback either completes or leaves the state exactly as it was.
- Expected Immunefi impact: Critical - permanent freezing of user funds.
- Fast validation: Sim minimal gas and inspect the resulting state.
