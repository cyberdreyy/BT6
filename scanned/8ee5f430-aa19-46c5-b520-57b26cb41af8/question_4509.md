# Q4509: Attacker-owned majority rounding - first delegator

## Question
Can an unprivileged attacker stake and unstake in a pattern that steers every rounding remainder toward the attacker's shares rather than the guarantee fund, as the first delegator, while `total_stake_shares` is still only the seeded `STAKE_SHARE_PRICE_GUARANTEE_FUND`, breaking the invariant that the guarantee fund absorbs rounding; no account gains NEAR from rounding alone, and leading to theft of staking rewards or fees owed to other parties?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_stake`
- Entrypoint: `stake(amount)` / `stake_all()` / `deposit_and_stake()` - any account, no role required
- Attacker controls: the staked amount, the call ordering inside a block, its own `unstaked` balance, and how many times it repeats
- Exploit idea: Stake and unstake in a pattern that steers every rounding remainder toward the attacker's shares rather than the guarantee fund, as the first delegator, while `total_stake_shares` is still only the seeded `STAKE_SHARE_PRICE_GUARANTEE_FUND`.
- Invariant to test: The guarantee fund absorbs rounding; no account gains NEAR from rounding alone.
- Expected Immunefi impact: High - theft of staking rewards or fees owed to other parties.
- Fast validation: Quickcheck round trips and assert the attacker's total balance never increases.
