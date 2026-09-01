# Q2129: Restake skipped while paused, reward still split - last block of an epoch

## Question
Can an unprivileged attacker ping while paused so rewards keep being credited against a stake that is no longer on chain, in the last block of an epoch so the state change lands before the reward is distributed, breaking the invariant that reward credited equals reward the protocol actually paid the pool, and leading to protocol accounting value diverges from reality and another party settles on it?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_ping / ping / on_stake_action`
- Entrypoint: `ping()` - callable by anyone; also reached from `deposit`, `stake`, `unstake`, `withdraw`
- Attacker controls: when the ping happens, how much NEAR is sitting on the account, and whether a stake action is in flight
- Exploit idea: Ping while paused so rewards keep being credited against a stake that is no longer on chain, in the last block of an epoch so the state change lands before the reward is distributed.
- Invariant to test: Reward credited equals reward the protocol actually paid the pool.
- Expected Immunefi impact: High - protocol accounting value diverges from reality and another party settles on it.
- Fast validation: Sim pause across an epoch and reconcile.
