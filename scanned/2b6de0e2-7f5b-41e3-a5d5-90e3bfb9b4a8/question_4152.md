# Q4152: Staking a balance that a failed withdraw left behind - row deleted by save

## Question
Can an unprivileged attacker stake `unstaked` NEAR that a previously failed `Promise::transfer` never actually returned to the pool, from an account whose row `internal_save_account` deleted when both balances reached zero, breaking the invariant that every yocto in an account's `unstaked` is backed by NEAR sitting in the pool account, and leading to protocol insolvency: recorded claims exceed the NEAR the contract actually holds?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_stake`
- Entrypoint: `stake(amount)` / `stake_all()` / `deposit_and_stake()` - any account, no role required
- Attacker controls: the staked amount, the call ordering inside a block, its own `unstaked` balance, and how many times it repeats
- Exploit idea: Stake `unstaked` NEAR that a previously failed `Promise::transfer` never actually returned to the pool, from an account whose row `internal_save_account` deleted when both balances reached zero.
- Invariant to test: Every yocto in an account's `unstaked` is backed by NEAR sitting in the pool account.
- Expected Immunefi impact: Critical - protocol insolvency: recorded claims exceed the NEAR the contract actually holds.
- Fast validation: Sim a failing withdraw target and then stake the phantom balance.
