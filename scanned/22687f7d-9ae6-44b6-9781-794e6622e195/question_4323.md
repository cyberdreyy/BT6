# Q4323: Reward distributed while a withdraw promise is unresolved - first delegator

## Question
Can an unprivileged attacker ping in the window where a withdrawal's NEAR has left the accounting but not yet the account, inflating `total_balance`, as the first delegator, while `total_stake_shares` is still only the seeded `STAKE_SHARE_PRICE_GUARANTEE_FUND`, breaking the invariant that `total_balance` at ping time excludes NEAR already debited from accounts, and leading to theft of staking rewards or fees owed to other parties?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_ping / ping / on_stake_action`
- Entrypoint: `ping()` - callable by anyone; also reached from `deposit`, `stake`, `unstake`, `withdraw`
- Attacker controls: when the ping happens, how much NEAR is sitting on the account, and whether a stake action is in flight
- Exploit idea: Ping in the window where a withdrawal's NEAR has left the accounting but not yet the account, inflating `total_balance`, as the first delegator, while `total_stake_shares` is still only the seeded `STAKE_SHARE_PRICE_GUARANTEE_FUND`.
- Invariant to test: `total_balance` at ping time excludes NEAR already debited from accounts.
- Expected Immunefi impact: High - theft of staking rewards or fees owed to other parties.
- Fast validation: Sim the interleaving and assert no phantom reward.
