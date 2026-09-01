# Q1955: Guarantee fund exhausted by design - amount = whole pool

## Question
Can an unprivileged attacker consume the fixed `STAKE_SHARE_PRICE_GUARANTEE_FUND` through rounding until the next honest unstake underflows the totals, with `amount` equal to the entire `total_staked_balance`, breaking the invariant that the fund is never a hard dependency for correctness of honest operations, and leading to permanent freezing of user funds?

## Target
- File/function: `staking-pool/src/internal.rs` - `num_shares_from_staked_amount_* / staked_amount_from_num_shares_*`
- Entrypoint: every value-moving pool method routes through these four helpers
- Attacker controls: the amounts fed into the helpers and the pool state at the time
- Exploit idea: Consume the fixed `STAKE_SHARE_PRICE_GUARANTEE_FUND` through rounding until the next honest unstake underflows the totals, with `amount` equal to the entire `total_staked_balance`.
- Invariant to test: The fund is never a hard dependency for correctness of honest operations.
- Expected Immunefi impact: Critical - permanent freezing of user funds.
- Fast validation: Loop rounding operations then assert an honest unstake succeeds.
