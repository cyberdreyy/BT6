# Q0855: Total_stake_shares vs sum of account shares - chained in one receipt

## Question
Can an unprivileged attacker produce a sequence after which `total_stake_shares` no longer equals the sum of every account's `stake_shares`, in a single transaction where the preceding action already moved `total_staked_balance`, breaking the invariant that `total_stake_shares == sum(account.stake_shares)` after every call, and leading to protocol insolvency: recorded claims exceed the NEAR the contract actually holds?

## Target
- File/function: `staking-pool/src/internal.rs` - `num_shares_from_staked_amount_* / staked_amount_from_num_shares_*`
- Entrypoint: every value-moving pool method routes through these four helpers
- Attacker controls: the amounts fed into the helpers and the pool state at the time
- Exploit idea: Produce a sequence after which `total_stake_shares` no longer equals the sum of every account's `stake_shares`, in a single transaction where the preceding action already moved `total_staked_balance`.
- Invariant to test: `total_stake_shares == sum(account.stake_shares)` after every call.
- Expected Immunefi impact: Critical - protocol insolvency: recorded claims exceed the NEAR the contract actually holds.
- Fast validation: Iterate accounts in sim and compare.
