# Q2741: Restake ordering across two stakes - amount = whole pool

## Question
Can an unprivileged attacker chain two `stake` calls in one transaction so the second is priced by a `total_staked_balance` the first inflated before any stake action confirmed, with `amount` equal to the entire `total_staked_balance`, breaking the invariant that the price used to mint shares matches the pool's settled staked balance, and leading to direct theft of delegator NEAR from the staking pool?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_stake`
- Entrypoint: `stake(amount)` / `stake_all()` / `deposit_and_stake()` - any account, no role required
- Attacker controls: the staked amount, the call ordering inside a block, its own `unstaked` balance, and how many times it repeats
- Exploit idea: Chain two `stake` calls in one transaction so the second is priced by a `total_staked_balance` the first inflated before any stake action confirmed, with `amount` equal to the entire `total_staked_balance`.
- Invariant to test: The price used to mint shares matches the pool's settled staked balance.
- Expected Immunefi impact: Critical - direct theft of delegator NEAR from the staking pool.
- Fast validation: Sim a batch of two stakes and reconcile totals afterwards.
