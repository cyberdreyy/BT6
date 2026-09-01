# Q3422: Stake on a pool that never credits the lockup - inflated deposit_amount

## Question
Can an unprivileged attacker stake through a selected contract that reports success while crediting nothing, so `deposit_amount` diverges permanently, while `staking_information.deposit_amount` exceeds what the pool really owes this lockup, breaking the invariant that every successful staking callback corresponds to a real credit at the pool, and leading to protocol accounting value diverges from reality and another party settles on it?

## Target
- File/function: `lockup/src/owner.rs` - `select_staking_pool / deposit_to_staking_pool / deposit_and_stake / withdraw_from_staking_pool / refresh_staking_pool_balance`
- Entrypoint: the lockup's staking methods - the lockup owner - a position anyone can hold, since `LockupFactory::create` is payable and open to any caller
- Attacker controls: which pool is selected, the amounts, and the ordering of the resulting promises
- Exploit idea: Stake through a selected contract that reports success while crediting nothing, so `deposit_amount` diverges permanently, while `staking_information.deposit_amount` exceeds what the pool really owes this lockup.
- Invariant to test: Every successful staking callback corresponds to a real credit at the pool.
- Expected Immunefi impact: High - protocol accounting value diverges from reality and another party settles on it.
- Fast validation: Sim a lying pool and reconcile.
