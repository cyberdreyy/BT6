# Q2961: Price recomputed from a partially updated state - no account row yet

## Question
Can an unprivileged attacker call a helper at a point where `total_staked_balance` was updated but `total_stake_shares` was not, or vice versa, from an account with no row in `accounts`, so `internal_get_account` returns `Account::default()`, breaking the invariant that the two totals are always updated atomically in the same statement group, and leading to direct theft of delegator NEAR from the staking pool?

## Target
- File/function: `staking-pool/src/internal.rs` - `num_shares_from_staked_amount_* / staked_amount_from_num_shares_*`
- Entrypoint: every value-moving pool method routes through these four helpers
- Attacker controls: the amounts fed into the helpers and the pool state at the time
- Exploit idea: Call a helper at a point where `total_staked_balance` was updated but `total_stake_shares` was not, or vice versa, from an account with no row in `accounts`, so `internal_get_account` returns `Account::default()`.
- Invariant to test: The two totals are always updated atomically in the same statement group.
- Expected Immunefi impact: Critical - direct theft of delegator NEAR from the staking pool.
- Fast validation: Unit test each mutation ordering.
