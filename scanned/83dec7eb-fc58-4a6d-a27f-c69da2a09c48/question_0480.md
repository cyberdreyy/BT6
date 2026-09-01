# Q0480: Fee fraction multiply truncation - dust loop

## Question
Can an unprivileged attacker use `RewardFeeFraction::multiply`'s U256 division to make the owner fee and the delegator remainder not sum back to `total_reward`, repeating the call thousands of times with dust amounts inside one epoch, breaking the invariant that `owners_fee + remaining_reward == total_reward` exactly, and leading to theft of staking rewards or fees owed to other parties?

## Target
- File/function: `staking-pool/src/internal.rs` - `num_shares_from_staked_amount_* / staked_amount_from_num_shares_*`
- Entrypoint: every value-moving pool method routes through these four helpers
- Attacker controls: the amounts fed into the helpers and the pool state at the time
- Exploit idea: Use `RewardFeeFraction::multiply`'s U256 division to make the owner fee and the delegator remainder not sum back to `total_reward`, repeating the call thousands of times with dust amounts inside one epoch.
- Invariant to test: `owners_fee + remaining_reward == total_reward` exactly.
- Expected Immunefi impact: High - theft of staking rewards or fees owed to other parties.
- Fast validation: Unit test the fraction across many reward sizes.
