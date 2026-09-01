# Q3602: Reward_fee applied to a negative-drift balance - no account row yet

## Question
Can an unprivileged attacker make `total_reward` include NEAR the pool merely got back from a refund, so the owner fee is taken from delegators' principal, from an account with no row in `accounts`, so `internal_get_account` returns `Account::default()`, breaking the invariant that the owner fee is only taken from genuine rewards, never from principal, and leading to theft of staking rewards or fees owed to other parties?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_ping / ping / on_stake_action`
- Entrypoint: `ping()` - callable by anyone; also reached from `deposit`, `stake`, `unstake`, `withdraw`
- Attacker controls: when the ping happens, how much NEAR is sitting on the account, and whether a stake action is in flight
- Exploit idea: Make `total_reward` include NEAR the pool merely got back from a refund, so the owner fee is taken from delegators' principal, from an account with no row in `accounts`, so `internal_get_account` returns `Account::default()`.
- Invariant to test: The owner fee is only taken from genuine rewards, never from principal.
- Expected Immunefi impact: High - theft of staking rewards or fees owed to other parties.
- Fast validation: Sim a refund into the pool then ping.
