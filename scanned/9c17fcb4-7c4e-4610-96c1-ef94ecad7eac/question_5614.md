# Q5614: Row deletion during unstake - across an epoch skip

## Question
Can an unprivileged attacker unstake so `internal_save_account` deletes the row while `total_stake_shares` still counts the burned shares, after deliberately letting several epochs pass with no `ping` at all, breaking the invariant that the sum of per-account `stake_shares` equals `total_stake_shares` after every call, and leading to protocol insolvency: recorded claims exceed the NEAR the contract actually holds?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::inner_unstake`
- Entrypoint: `unstake(amount)` / `unstake_all()` - any delegator
- Attacker controls: the unstaked amount, timing relative to `internal_ping`, and the number of repetitions
- Exploit idea: Unstake so `internal_save_account` deletes the row while `total_stake_shares` still counts the burned shares, after deliberately letting several epochs pass with no `ping` at all.
- Invariant to test: The sum of per-account `stake_shares` equals `total_stake_shares` after every call.
- Expected Immunefi impact: Critical - protocol insolvency: recorded claims exceed the NEAR the contract actually holds.
- Fast validation: Iterate `get_accounts` in sim and compare against `total_stake_shares`.
