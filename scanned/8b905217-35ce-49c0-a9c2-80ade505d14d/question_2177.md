# Q2177: Row deletion during unstake - last block of an epoch

## Question
Can an unprivileged attacker unstake so `internal_save_account` deletes the row while `total_stake_shares` still counts the burned shares, in the last block of an epoch so the state change lands before the reward is distributed, breaking the invariant that the sum of per-account `stake_shares` equals `total_stake_shares` after every call, and leading to protocol insolvency: recorded claims exceed the NEAR the contract actually holds?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::inner_unstake`
- Entrypoint: `unstake(amount)` / `unstake_all()` - any delegator
- Attacker controls: the unstaked amount, timing relative to `internal_ping`, and the number of repetitions
- Exploit idea: Unstake so `internal_save_account` deletes the row while `total_stake_shares` still counts the burned shares, in the last block of an epoch so the state change lands before the reward is distributed.
- Invariant to test: The sum of per-account `stake_shares` equals `total_stake_shares` after every call.
- Expected Immunefi impact: Critical - protocol insolvency: recorded claims exceed the NEAR the contract actually holds.
- Fast validation: Iterate `get_accounts` in sim and compare against `total_stake_shares`.
