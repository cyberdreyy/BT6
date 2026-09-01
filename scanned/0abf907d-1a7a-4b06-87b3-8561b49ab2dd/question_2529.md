# Q2529: Price jump exploited across one receipt - u128::MAX

## Question
Can an unprivileged attacker act on both sides of a large price change inside one transaction so the mint and the burn use different prices, with `amount = u128::MAX` so the U256 product dwarfs any real balance, breaking the invariant that one transaction prices all of its share operations consistently, and leading to direct theft of delegator NEAR from the staking pool?

## Target
- File/function: `staking-pool/src/internal.rs` - `num_shares_from_staked_amount_* / staked_amount_from_num_shares_*`
- Entrypoint: every value-moving pool method routes through these four helpers
- Attacker controls: the amounts fed into the helpers and the pool state at the time
- Exploit idea: Act on both sides of a large price change inside one transaction so the mint and the burn use different prices, with `amount = u128::MAX` so the U256 product dwarfs any real balance.
- Invariant to test: One transaction prices all of its share operations consistently.
- Expected Immunefi impact: Critical - direct theft of delegator NEAR from the staking pool.
- Fast validation: Sim a batched mint+burn across a reward.
