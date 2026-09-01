# Q4029: Donation counted as reward - first delegator

## Question
Can an unprivileged attacker transfer NEAR straight to the pool account and then call `ping`, so `total_balance - last_total_balance` treats the donation as an epoch reward split between the owner fee and existing shares, as the first delegator, while `total_stake_shares` is still only the seeded `STAKE_SHARE_PRICE_GUARANTEE_FUND`, breaking the invariant that `total_reward` counts only NEAR credited by the staking mechanism, and leading to theft of staking rewards or fees owed to other parties?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_ping / ping / on_stake_action`
- Entrypoint: `ping()` - callable by anyone; also reached from `deposit`, `stake`, `unstake`, `withdraw`
- Attacker controls: when the ping happens, how much NEAR is sitting on the account, and whether a stake action is in flight
- Exploit idea: Transfer NEAR straight to the pool account and then call `ping`, so `total_balance - last_total_balance` treats the donation as an epoch reward split between the owner fee and existing shares, as the first delegator, while `total_stake_shares` is still only the seeded `STAKE_SHARE_PRICE_GUARANTEE_FUND`.
- Invariant to test: `total_reward` counts only NEAR credited by the staking mechanism.
- Expected Immunefi impact: High - theft of staking rewards or fees owed to other parties.
- Fast validation: Sim a bare transfer + ping and assert delegator claims do not grow.
