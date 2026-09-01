# Q3368: Reward attributed to shares bought after it accrued - no account row yet

## Question
Can an unprivileged attacker delay `ping` until after staking, so a reward earned in an earlier epoch is priced into shares that did not exist during it, from an account with no row in `accounts`, so `internal_get_account` returns `Account::default()`, breaking the invariant that reward for epoch E accrues only to shares outstanding during E, and leading to theft of staking rewards or fees owed to other parties?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_ping / ping / on_stake_action`
- Entrypoint: `ping()` - callable by anyone; also reached from `deposit`, `stake`, `unstake`, `withdraw`
- Attacker controls: when the ping happens, how much NEAR is sitting on the account, and whether a stake action is in flight
- Exploit idea: Delay `ping` until after staking, so a reward earned in an earlier epoch is priced into shares that did not exist during it, from an account with no row in `accounts`, so `internal_get_account` returns `Account::default()`.
- Invariant to test: Reward for epoch E accrues only to shares outstanding during E.
- Expected Immunefi impact: High - theft of staking rewards or fees owed to other parties.
- Fast validation: Sim skipped epochs then compare payouts.
