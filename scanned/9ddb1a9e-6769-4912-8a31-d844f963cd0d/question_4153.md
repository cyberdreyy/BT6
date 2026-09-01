# Q4153: U256 truncation on the unstake path - first delegator

## Question
Can an unprivileged attacker supply an `amount` where the `U256` numerator in `num_shares_from_staked_amount_rounded_up` overflows `u128` on `.as_u128()`, as the first delegator, while `total_stake_shares` is still only the seeded `STAKE_SHARE_PRICE_GUARANTEE_FUND`, breaking the invariant that share arithmetic never wraps for any representable amount, and leading to direct theft of delegator NEAR from the staking pool?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::inner_unstake`
- Entrypoint: `unstake(amount)` / `unstake_all()` - any delegator
- Attacker controls: the unstaked amount, timing relative to `internal_ping`, and the number of repetitions
- Exploit idea: Supply an `amount` where the `U256` numerator in `num_shares_from_staked_amount_rounded_up` overflows `u128` on `.as_u128()`, as the first delegator, while `total_stake_shares` is still only the seeded `STAKE_SHARE_PRICE_GUARANTEE_FUND`.
- Invariant to test: Share arithmetic never wraps for any representable amount.
- Expected Immunefi impact: Critical - direct theft of delegator NEAR from the staking pool.
- Fast validation: Direct unit test of the helper with extreme inputs.
