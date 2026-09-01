# Q3465: Conversion asymmetry across deposit and withdrawal - first delegator

## Question
Can an unprivileged attacker combine the rounded-down mint with the rounded-up redeem so a single round trip nets positive at scale, as the first delegator, while `total_stake_shares` is still only the seeded `STAKE_SHARE_PRICE_GUARANTEE_FUND`, breaking the invariant that a deposit-stake-unstake-withdraw round trip is never profitable, and leading to direct theft of delegator NEAR from the staking pool?

## Target
- File/function: `staking-pool/src/internal.rs` - `num_shares_from_staked_amount_* / staked_amount_from_num_shares_*`
- Entrypoint: every value-moving pool method routes through these four helpers
- Attacker controls: the amounts fed into the helpers and the pool state at the time
- Exploit idea: Combine the rounded-down mint with the rounded-up redeem so a single round trip nets positive at scale, as the first delegator, while `total_stake_shares` is still only the seeded `STAKE_SHARE_PRICE_GUARANTEE_FUND`.
- Invariant to test: A deposit-stake-unstake-withdraw round trip is never profitable.
- Expected Immunefi impact: Critical - direct theft of delegator NEAR from the staking pool.
- Fast validation: Quickcheck the full round trip in sim.
