# Q3902: Rounded_up numerator overflow - after a bare donation

## Question
Can an unprivileged attacker hit the `+ (total_staked_balance - 1)` term in `num_shares_from_staked_amount_rounded_up` with values that carry past the U256 range, after first sending NEAR straight to the pool account with a bare `Transfer` outside any method, breaking the invariant that the rounding-up adjustment never changes the magnitude of the result, and leading to direct theft of delegator NEAR from the staking pool?

## Target
- File/function: `staking-pool/src/internal.rs` - `num_shares_from_staked_amount_* / staked_amount_from_num_shares_*`
- Entrypoint: every value-moving pool method routes through these four helpers
- Attacker controls: the amounts fed into the helpers and the pool state at the time
- Exploit idea: Hit the `+ (total_staked_balance - 1)` term in `num_shares_from_staked_amount_rounded_up` with values that carry past the U256 range, after first sending NEAR straight to the pool account with a bare `Transfer` outside any method.
- Invariant to test: The rounding-up adjustment never changes the magnitude of the result.
- Expected Immunefi impact: Critical - direct theft of delegator NEAR from the staking pool.
- Fast validation: Unit test the helper near the boundary.
