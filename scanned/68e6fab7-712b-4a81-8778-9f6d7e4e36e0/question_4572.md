# Q4572: Stake against a default account row - first delegator

## Question
Can an unprivileged attacker stake from an account that has no row in `accounts`, relying on `Account::default()` to supply zeros that later underflow or over-credit, as the first delegator, while `total_stake_shares` is still only the seeded `STAKE_SHARE_PRICE_GUARANTEE_FUND`, breaking the invariant that an account with no stored row can never end a call with a positive `stake_shares`, and leading to direct theft of delegator NEAR from the staking pool?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_stake`
- Entrypoint: `stake(amount)` / `stake_all()` / `deposit_and_stake()` - any account, no role required
- Attacker controls: the staked amount, the call ordering inside a block, its own `unstaked` balance, and how many times it repeats
- Exploit idea: Stake from an account that has no row in `accounts`, relying on `Account::default()` to supply zeros that later underflow or over-credit, as the first delegator, while `total_stake_shares` is still only the seeded `STAKE_SHARE_PRICE_GUARANTEE_FUND`.
- Invariant to test: An account with no stored row can never end a call with a positive `stake_shares`.
- Expected Immunefi impact: Critical - direct theft of delegator NEAR from the staking pool.
- Fast validation: Unit test staking from an unknown account id.
