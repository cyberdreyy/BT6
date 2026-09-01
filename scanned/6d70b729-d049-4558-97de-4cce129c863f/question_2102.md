# Q2102: Total_stake_shares underflow path - last block of an epoch

## Question
Can an unprivileged attacker drive `total_stake_shares` down through repeated unstakes until the subtraction in `inner_unstake` meets the seeded guarantee shares, in the last block of an epoch so the state change lands before the reward is distributed, breaking the invariant that `total_stake_shares` never drops below the shares the pool seeded at initialisation, and leading to permanent freezing of user funds?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::inner_unstake`
- Entrypoint: `unstake(amount)` / `unstake_all()` - any delegator
- Attacker controls: the unstaked amount, timing relative to `internal_ping`, and the number of repetitions
- Exploit idea: Drive `total_stake_shares` down through repeated unstakes until the subtraction in `inner_unstake` meets the seeded guarantee shares, in the last block of an epoch so the state change lands before the reward is distributed.
- Invariant to test: `total_stake_shares` never drops below the shares the pool seeded at initialisation.
- Expected Immunefi impact: Critical - permanent freezing of user funds.
- Fast validation: Sim mass unstake and assert the floor holds and no method panics afterwards.
