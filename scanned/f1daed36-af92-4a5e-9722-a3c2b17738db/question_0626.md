# Q0626: Minimum share price crossing - dust loop

## Question
Can an unprivileged attacker stake an amount just above the point where `num_shares` becomes 1, so a single share is minted for far less NEAR than a share is worth, repeating the call thousands of times with dust amounts inside one epoch, breaking the invariant that the NEAR charged per minted share is at least the current share price, and leading to direct theft of delegator NEAR from the staking pool?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_stake`
- Entrypoint: `stake(amount)` / `stake_all()` / `deposit_and_stake()` - any account, no role required
- Attacker controls: the staked amount, the call ordering inside a block, its own `unstaked` balance, and how many times it repeats
- Exploit idea: Stake an amount just above the point where `num_shares` becomes 1, so a single share is minted for far less NEAR than a share is worth, repeating the call thousands of times with dust amounts inside one epoch.
- Invariant to test: The NEAR charged per minted share is at least the current share price.
- Expected Immunefi impact: Critical - direct theft of delegator NEAR from the staking pool.
- Fast validation: Property test over a wide price range asserting charged/share >= price.
