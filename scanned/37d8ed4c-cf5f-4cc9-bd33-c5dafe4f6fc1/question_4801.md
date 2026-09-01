# Q4801: Total_stake_shares vs sum of account shares - across an epoch skip

## Question
Can an unprivileged attacker produce a sequence after which `total_stake_shares` no longer equals the sum of every account's `stake_shares`, after deliberately letting several epochs pass with no `ping` at all, breaking the invariant that `total_stake_shares == sum(account.stake_shares)` after every call, and leading to protocol insolvency: recorded claims exceed the NEAR the contract actually holds?

## Target
- File/function: `staking-pool/src/internal.rs` - `num_shares_from_staked_amount_* / staked_amount_from_num_shares_*`
- Entrypoint: every value-moving pool method routes through these four helpers
- Attacker controls: the amounts fed into the helpers and the pool state at the time
- Exploit idea: Produce a sequence after which `total_stake_shares` no longer equals the sum of every account's `stake_shares`, after deliberately letting several epochs pass with no `ping` at all.
- Invariant to test: `total_stake_shares == sum(account.stake_shares)` after every call.
- Expected Immunefi impact: Critical - protocol insolvency: recorded claims exceed the NEAR the contract actually holds.
- Fast validation: Iterate accounts in sim and compare.
