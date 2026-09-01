# Q4197: On_stake_action reachable without a stake - first delegator

## Question
Can an unprivileged attacker invoke `on_stake_action` in a way that satisfies its `current_account_id == predecessor_account_id` check without a genuine staking promise behind it, as the first delegator, while `total_stake_shares` is still only the seeded `STAKE_SHARE_PRICE_GUARANTEE_FUND`, breaking the invariant that `on_stake_action` only runs as the callback of a stake action this contract scheduled, and leading to protocol accounting value diverges from reality and another party settles on it?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_ping / ping / on_stake_action`
- Entrypoint: `ping()` - callable by anyone; also reached from `deposit`, `stake`, `unstake`, `withdraw`
- Attacker controls: when the ping happens, how much NEAR is sitting on the account, and whether a stake action is in flight
- Exploit idea: Invoke `on_stake_action` in a way that satisfies its `current_account_id == predecessor_account_id` check without a genuine staking promise behind it, as the first delegator, while `total_stake_shares` is still only the seeded `STAKE_SHARE_PRICE_GUARANTEE_FUND`.
- Invariant to test: `on_stake_action` only runs as the callback of a stake action this contract scheduled.
- Expected Immunefi impact: High - protocol accounting value diverges from reality and another party settles on it.
- Fast validation: Sim a crafted callback receipt and assert it is rejected.
