# Q5998: Restake skipped while paused, reward still split - right after pool creation

## Question
Can an unprivileged attacker ping while paused so rewards keep being credited against a stake that is no longer on chain, on a pool created moments earlier through the public `create_staking_pool`, breaking the invariant that reward credited equals reward the protocol actually paid the pool, and leading to protocol accounting value diverges from reality and another party settles on it?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_ping / ping / on_stake_action`
- Entrypoint: `ping()` - callable by anyone; also reached from `deposit`, `stake`, `unstake`, `withdraw`
- Attacker controls: when the ping happens, how much NEAR is sitting on the account, and whether a stake action is in flight
- Exploit idea: Ping while paused so rewards keep being credited against a stake that is no longer on chain, on a pool created moments earlier through the public `create_staking_pool`.
- Invariant to test: Reward credited equals reward the protocol actually paid the pool.
- Expected Immunefi impact: High - protocol accounting value diverges from reality and another party settles on it.
- Fast validation: Sim pause across an epoch and reconcile.
