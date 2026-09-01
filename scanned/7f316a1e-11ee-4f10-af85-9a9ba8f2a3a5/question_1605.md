# Q1605: Share price manipulated below one - last block of an epoch

## Question
Can an unprivileged attacker drive `total_staked_balance` below `total_stake_shares` so the price drops under one and the rounding directions reverse in the attacker's favour, in the last block of an epoch so the state change lands before the reward is distributed, breaking the invariant that the share price is always at least one, and leading to direct theft of delegator NEAR from the staking pool?

## Target
- File/function: `staking-pool/src/internal.rs` - `num_shares_from_staked_amount_* / staked_amount_from_num_shares_*`
- Entrypoint: every value-moving pool method routes through these four helpers
- Attacker controls: the amounts fed into the helpers and the pool state at the time
- Exploit idea: Drive `total_staked_balance` below `total_stake_shares` so the price drops under one and the rounding directions reverse in the attacker's favour, in the last block of an epoch so the state change lands before the reward is distributed.
- Invariant to test: The share price is always at least one.
- Expected Immunefi impact: Critical - direct theft of delegator NEAR from the staking pool.
- Fast validation: Sim a sequence that lowers the price and assert the floor.
