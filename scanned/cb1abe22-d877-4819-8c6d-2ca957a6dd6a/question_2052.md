# Q2052: Unstake after a bare donation moved the price - last block of an epoch

## Question
Can an unprivileged attacker donate NEAR to the pool, ping to raise the price, then unstake shares bought before the donation, in the last block of an epoch so the state change lands before the reward is distributed, breaking the invariant that an account cannot withdraw more than its deposits plus its proportional share of real rewards, and leading to theft of staking rewards or fees owed to other parties?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::inner_unstake`
- Entrypoint: `unstake(amount)` / `unstake_all()` - any delegator
- Attacker controls: the unstaked amount, timing relative to `internal_ping`, and the number of repetitions
- Exploit idea: Donate NEAR to the pool, ping to raise the price, then unstake shares bought before the donation, in the last block of an epoch so the state change lands before the reward is distributed.
- Invariant to test: An account cannot withdraw more than its deposits plus its proportional share of real rewards.
- Expected Immunefi impact: High - theft of staking rewards or fees owed to other parties.
- Fast validation: Sim donation then unstake, comparing NEAR out against NEAR in.
