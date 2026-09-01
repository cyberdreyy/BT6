# Q2745: As_u128 truncation of a U256 result - no account row yet

## Question
Can an unprivileged attacker supply inputs where the U256 quotient exceeds `u128::MAX` and `.as_u128()` truncates instead of failing, from an account with no row in `accounts`, so `internal_get_account` returns `Account::default()`, breaking the invariant that the helpers are monotonic and never wrap for representable balances, and leading to direct theft of delegator NEAR from the staking pool?

## Target
- File/function: `staking-pool/src/internal.rs` - `num_shares_from_staked_amount_* / staked_amount_from_num_shares_*`
- Entrypoint: every value-moving pool method routes through these four helpers
- Attacker controls: the amounts fed into the helpers and the pool state at the time
- Exploit idea: Supply inputs where the U256 quotient exceeds `u128::MAX` and `.as_u128()` truncates instead of failing, from an account with no row in `accounts`, so `internal_get_account` returns `Account::default()`.
- Invariant to test: The helpers are monotonic and never wrap for representable balances.
- Expected Immunefi impact: Critical - direct theft of delegator NEAR from the staking pool.
- Fast validation: Unit test with extreme inputs.
