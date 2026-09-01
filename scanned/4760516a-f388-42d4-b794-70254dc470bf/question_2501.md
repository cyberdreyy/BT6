# Q2501: Buying shares at the pre-reward price - amount = whole pool

## Question
Can an unprivileged attacker stake in the same receipt in which `internal_ping` folded a fresh epoch reward into `total_staked_balance`, so the price used for minting is not the price the reward created, with `amount` equal to the entire `total_staked_balance`, breaking the invariant that shares minted in receipt R are priced by the `total_staked_balance` that includes every reward already credited in R, and leading to theft of staking rewards or fees owed to other parties?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_stake`
- Entrypoint: `stake(amount)` / `stake_all()` / `deposit_and_stake()` - any account, no role required
- Attacker controls: the staked amount, the call ordering inside a block, its own `unstaked` balance, and how many times it repeats
- Exploit idea: Stake in the same receipt in which `internal_ping` folded a fresh epoch reward into `total_staked_balance`, so the price used for minting is not the price the reward created, with `amount` equal to the entire `total_staked_balance`.
- Invariant to test: Shares minted in receipt R are priced by the `total_staked_balance` that includes every reward already credited in R.
- Expected Immunefi impact: High - theft of staking rewards or fees owed to other parties.
- Fast validation: Sim test: reward the pool, then stake and immediately unstake, asserting no reward accrues to the new shares.
