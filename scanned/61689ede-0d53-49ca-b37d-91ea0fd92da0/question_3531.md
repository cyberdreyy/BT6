# Q3531: Unstake with the pool paused - no account row yet

## Question
Can an unprivileged attacker unstake while `paused` so `internal_restake` never re-stakes, leaving `total_staked_balance` and the on-chain locked balance divergent, from an account with no row in `accounts`, so `internal_get_account` returns `Account::default()`, breaking the invariant that `total_staked_balance` matches `env::account_locked_balance()` once all stake actions settle, and leading to protocol accounting value diverges from reality and another party settles on it?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::inner_unstake`
- Entrypoint: `unstake(amount)` / `unstake_all()` - any delegator
- Attacker controls: the unstaked amount, timing relative to `internal_ping`, and the number of repetitions
- Exploit idea: Unstake while `paused` so `internal_restake` never re-stakes, leaving `total_staked_balance` and the on-chain locked balance divergent, from an account with no row in `accounts`, so `internal_get_account` returns `Account::default()`.
- Invariant to test: `total_staked_balance` matches `env::account_locked_balance()` once all stake actions settle.
- Expected Immunefi impact: High - protocol accounting value diverges from reality and another party settles on it.
- Fast validation: Sim pause + unstake and reconcile the two values.
