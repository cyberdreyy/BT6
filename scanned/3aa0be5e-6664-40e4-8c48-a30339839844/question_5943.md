# Q5943: Reward distributed while a withdraw promise is unresolved - with the reward fee at one

## Question
Can an unprivileged attacker ping in the window where a withdrawal's NEAR has left the accounting but not yet the account, inflating `total_balance`, on a pool whose `reward_fee_fraction` is the full 1/1, breaking the invariant that `total_balance` at ping time excludes NEAR already debited from accounts, and leading to theft of staking rewards or fees owed to other parties?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_ping / ping / on_stake_action`
- Entrypoint: `ping()` - callable by anyone; also reached from `deposit`, `stake`, `unstake`, `withdraw`
- Attacker controls: when the ping happens, how much NEAR is sitting on the account, and whether a stake action is in flight
- Exploit idea: Ping in the window where a withdrawal's NEAR has left the accounting but not yet the account, inflating `total_balance`, on a pool whose `reward_fee_fraction` is the full 1/1.
- Invariant to test: `total_balance` at ping time excludes NEAR already debited from accounts.
- Expected Immunefi impact: High - theft of staking rewards or fees owed to other parties.
- Fast validation: Sim the interleaving and assert no phantom reward.
