# Q4855: Division-by-zero assertions as a wedge - with the reward fee at zero

## Question
Can an unprivileged attacker reach a state where `total_staked_balance == 0` or `total_stake_shares == 0` so every conversion asserts and the pool is bricked, on a pool whose `reward_fee_fraction` numerator is zero, breaking the invariant that the seeded guarantee fund keeps both totals strictly positive forever, and leading to permanent freezing of user funds?

## Target
- File/function: `staking-pool/src/internal.rs` - `num_shares_from_staked_amount_* / staked_amount_from_num_shares_*`
- Entrypoint: every value-moving pool method routes through these four helpers
- Attacker controls: the amounts fed into the helpers and the pool state at the time
- Exploit idea: Reach a state where `total_staked_balance == 0` or `total_stake_shares == 0` so every conversion asserts and the pool is bricked, on a pool whose `reward_fee_fraction` numerator is zero.
- Invariant to test: The seeded guarantee fund keeps both totals strictly positive forever.
- Expected Immunefi impact: Critical - permanent freezing of user funds.
- Fast validation: Drive the totals down in sim and assert the helpers still work.
