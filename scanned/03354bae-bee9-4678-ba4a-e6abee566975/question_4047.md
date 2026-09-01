# Q4047: Staking while paused overstates total_staked_balance - row deleted by save

## Question
Can an unprivileged attacker stake while `paused == true`, so `total_staked_balance` grows although `internal_restake` returns without staking anything on chain, from an account whose row `internal_save_account` deleted when both balances reached zero, breaking the invariant that `total_staked_balance <= env::account_locked_balance()` whenever the pool is not mid-restake, and leading to protocol accounting value diverges from reality and another party settles on it?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_stake`
- Entrypoint: `stake(amount)` / `stake_all()` / `deposit_and_stake()` - any account, no role required
- Attacker controls: the staked amount, the call ordering inside a block, its own `unstaked` balance, and how many times it repeats
- Exploit idea: Stake while `paused == true`, so `total_staked_balance` grows although `internal_restake` returns without staking anything on chain, from an account whose row `internal_save_account` deleted when both balances reached zero.
- Invariant to test: `total_staked_balance <= env::account_locked_balance()` whenever the pool is not mid-restake.
- Expected Immunefi impact: High - protocol accounting value diverges from reality and another party settles on it.
- Fast validation: Pause via owner in sim, stake as an attacker, assert `total_staked_balance` vs `account_locked_balance`.
