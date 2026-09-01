# Q3556: Failed stake action leaves locked balance stale - no account row yet

## Question
Can an unprivileged attacker make the stake action fail while `env::account_locked_balance() > 0`, so the compensating `stake(0)` and `total_staked_balance` disagree, from an account with no row in `accounts`, so `internal_get_account` returns `Account::default()`, breaking the invariant that after `on_stake_action`, `total_staked_balance == env::account_locked_balance()`, and leading to protocol insolvency: recorded claims exceed the NEAR the contract actually holds?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_ping / ping / on_stake_action`
- Entrypoint: `ping()` - callable by anyone; also reached from `deposit`, `stake`, `unstake`, `withdraw`
- Attacker controls: when the ping happens, how much NEAR is sitting on the account, and whether a stake action is in flight
- Exploit idea: Make the stake action fail while `env::account_locked_balance() > 0`, so the compensating `stake(0)` and `total_staked_balance` disagree, from an account with no row in `accounts`, so `internal_get_account` returns `Account::default()`.
- Invariant to test: After `on_stake_action`, `total_staked_balance == env::account_locked_balance()`.
- Expected Immunefi impact: Critical - protocol insolvency: recorded claims exceed the NEAR the contract actually holds.
- Fast validation: Sim a failing stake action and compare both values.
