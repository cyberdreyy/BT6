# Q3603: As_u128 truncation of a U256 result - attacker holds most shares

## Question
Can an unprivileged attacker supply inputs where the U256 quotient exceeds `u128::MAX` and `.as_u128()` truncates instead of failing, while holding the majority of `total_stake_shares`, so rounding accrues mostly to the attacker, breaking the invariant that the helpers are monotonic and never wrap for representable balances, and leading to direct theft of delegator NEAR from the staking pool?

## Target
- File/function: `staking-pool/src/internal.rs` - `num_shares_from_staked_amount_* / staked_amount_from_num_shares_*`
- Entrypoint: every value-moving pool method routes through these four helpers
- Attacker controls: the amounts fed into the helpers and the pool state at the time
- Exploit idea: Supply inputs where the U256 quotient exceeds `u128::MAX` and `.as_u128()` truncates instead of failing, while holding the majority of `total_stake_shares`, so rounding accrues mostly to the attacker.
- Invariant to test: The helpers are monotonic and never wrap for representable balances.
- Expected Immunefi impact: Critical - direct theft of delegator NEAR from the staking pool.
- Fast validation: Unit test with extreme inputs.
