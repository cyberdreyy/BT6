# Q0131: Sum of rows vs totals after row churn - 1-yocto amount

## Question
Can an unprivileged attacker churn rows through creation and deletion until the sum of rows no longer matches `total_stake_shares` and `last_total_balance`, with `amount = 1` yoctoNEAR so every U256 division truncates, breaking the invariant that the totals always equal the aggregate of the stored rows, and leading to protocol insolvency: recorded claims exceed the NEAR the contract actually holds?

## Target
- File/function: `staking-pool/src/internal.rs` - `internal_get_account / internal_save_account / get_account`
- Entrypoint: any delegator method, plus the public view methods `get_account`, `get_accounts`, `get_number_of_accounts`
- Attacker controls: when its row exists, when it is deleted, and what an integrator reads from the views
- Exploit idea: Churn rows through creation and deletion until the sum of rows no longer matches `total_stake_shares` and `last_total_balance`, with `amount = 1` yoctoNEAR so every U256 division truncates.
- Invariant to test: The totals always equal the aggregate of the stored rows.
- Expected Immunefi impact: Critical - protocol insolvency: recorded claims exceed the NEAR the contract actually holds.
- Fast validation: Iterate `get_accounts` in sim and reconcile.
