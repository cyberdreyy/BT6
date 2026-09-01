# Q3600: Unstake in the same receipt as a reward - no account row yet

## Question
Can an unprivileged attacker unstake right after `internal_ping` credited the epoch reward, capturing yield for shares held only inside that receipt, from an account with no row in `accounts`, so `internal_get_account` returns `Account::default()`, breaking the invariant that reward attribution matches the shares held across the rewarded epoch, and leading to theft of staking rewards or fees owed to other parties?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::inner_unstake`
- Entrypoint: `unstake(amount)` / `unstake_all()` - any delegator
- Attacker controls: the unstaked amount, timing relative to `internal_ping`, and the number of repetitions
- Exploit idea: Unstake right after `internal_ping` credited the epoch reward, capturing yield for shares held only inside that receipt, from an account with no row in `accounts`, so `internal_get_account` returns `Account::default()`.
- Invariant to test: Reward attribution matches the shares held across the rewarded epoch.
- Expected Immunefi impact: High - theft of staking rewards or fees owed to other parties.
- Fast validation: Compare two delegators' payouts in sim.
