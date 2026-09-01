# Q4069: Guarantee fund drained one yocto at a time - first delegator

## Question
Can an unprivileged attacker repeat minimal unstakes so each one takes the rounding difference out of `STAKE_SHARE_PRICE_GUARANTEE_FUND` until it is exhausted and the price starts to fall, as the first delegator, while `total_stake_shares` is still only the seeded `STAKE_SHARE_PRICE_GUARANTEE_FUND`, breaking the invariant that the share price never decreases across any sequence of user actions, and leading to direct theft of delegator NEAR from the staking pool?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::inner_unstake`
- Entrypoint: `unstake(amount)` / `unstake_all()` - any delegator
- Attacker controls: the unstaked amount, timing relative to `internal_ping`, and the number of repetitions
- Exploit idea: Repeat minimal unstakes so each one takes the rounding difference out of `STAKE_SHARE_PRICE_GUARANTEE_FUND` until it is exhausted and the price starts to fall, as the first delegator, while `total_stake_shares` is still only the seeded `STAKE_SHARE_PRICE_GUARANTEE_FUND`.
- Invariant to test: The share price never decreases across any sequence of user actions.
- Expected Immunefi impact: Critical - direct theft of delegator NEAR from the staking pool.
- Fast validation: Loop unstakes in sim and assert the price sequence is non-decreasing.
