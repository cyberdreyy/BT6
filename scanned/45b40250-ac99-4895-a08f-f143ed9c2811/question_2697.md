# Q2697: Rounded_up/rounded_down pair inverted - no account row yet

## Question
Can an unprivileged attacker find an amount where `staked_amount_from_num_shares_rounded_up(num_shares_from_staked_amount_rounded_down(a)) > a`, from an account with no row in `accounts`, so `internal_get_account` returns `Account::default()`, breaking the invariant that converting NEAR to shares and back never yields more NEAR than went in, and leading to direct theft of delegator NEAR from the staking pool?

## Target
- File/function: `staking-pool/src/internal.rs` - `num_shares_from_staked_amount_* / staked_amount_from_num_shares_*`
- Entrypoint: every value-moving pool method routes through these four helpers
- Attacker controls: the amounts fed into the helpers and the pool state at the time
- Exploit idea: Find an amount where `staked_amount_from_num_shares_rounded_up(num_shares_from_staked_amount_rounded_down(a)) > a`, from an account with no row in `accounts`, so `internal_get_account` returns `Account::default()`.
- Invariant to test: Converting NEAR to shares and back never yields more NEAR than went in.
- Expected Immunefi impact: Critical - direct theft of delegator NEAR from the staking pool.
- Fast validation: Quickcheck the round trip over random pool states.
