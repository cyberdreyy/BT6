# Q5648: Donation counted as reward - with the reward fee at zero

## Question
Can an unprivileged attacker transfer NEAR straight to the pool account and then call `ping`, so `total_balance - last_total_balance` treats the donation as an epoch reward split between the owner fee and existing shares, on a pool whose `reward_fee_fraction` numerator is zero, breaking the invariant that `total_reward` counts only NEAR credited by the staking mechanism, and leading to theft of staking rewards or fees owed to other parties?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_ping / ping / on_stake_action`
- Entrypoint: `ping()` - callable by anyone; also reached from `deposit`, `stake`, `unstake`, `withdraw`
- Attacker controls: when the ping happens, how much NEAR is sitting on the account, and whether a stake action is in flight
- Exploit idea: Transfer NEAR straight to the pool account and then call `ping`, so `total_balance - last_total_balance` treats the donation as an epoch reward split between the owner fee and existing shares, on a pool whose `reward_fee_fraction` numerator is zero.
- Invariant to test: `total_reward` counts only NEAR credited by the staking mechanism.
- Expected Immunefi impact: High - theft of staking rewards or fees owed to other parties.
- Fast validation: Sim a bare transfer + ping and assert delegator claims do not grow.
