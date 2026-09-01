# Q4009: Conversion asymmetry across deposit and withdrawal - after a bare donation

## Question
Can an unprivileged attacker combine the rounded-down mint with the rounded-up redeem so a single round trip nets positive at scale, after first sending NEAR straight to the pool account with a bare `Transfer` outside any method, breaking the invariant that a deposit-stake-unstake-withdraw round trip is never profitable, and leading to direct theft of delegator NEAR from the staking pool?

## Target
- File/function: `staking-pool/src/internal.rs` - `num_shares_from_staked_amount_* / staked_amount_from_num_shares_*`
- Entrypoint: every value-moving pool method routes through these four helpers
- Attacker controls: the amounts fed into the helpers and the pool state at the time
- Exploit idea: Combine the rounded-down mint with the rounded-up redeem so a single round trip nets positive at scale, after first sending NEAR straight to the pool account with a bare `Transfer` outside any method.
- Invariant to test: A deposit-stake-unstake-withdraw round trip is never profitable.
- Expected Immunefi impact: Critical - direct theft of delegator NEAR from the staking pool.
- Fast validation: Quickcheck the full round trip in sim.
