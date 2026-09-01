# Q0776: Stake against a default account row - dust loop

## Question
Can an unprivileged attacker stake from an account that has no row in `accounts`, relying on `Account::default()` to supply zeros that later underflow or over-credit, repeating the call thousands of times with dust amounts inside one epoch, breaking the invariant that an account with no stored row can never end a call with a positive `stake_shares`, and leading to direct theft of delegator NEAR from the staking pool?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_stake`
- Entrypoint: `stake(amount)` / `stake_all()` / `deposit_and_stake()` - any account, no role required
- Attacker controls: the staked amount, the call ordering inside a block, its own `unstaked` balance, and how many times it repeats
- Exploit idea: Stake from an account that has no row in `accounts`, relying on `Account::default()` to supply zeros that later underflow or over-credit, repeating the call thousands of times with dust amounts inside one epoch.
- Invariant to test: An account with no stored row can never end a call with a positive `stake_shares`.
- Expected Immunefi impact: Critical - direct theft of delegator NEAR from the staking pool.
- Fast validation: Unit test staking from an unknown account id.
