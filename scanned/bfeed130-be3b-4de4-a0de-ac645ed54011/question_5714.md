# Q5714: Epoch skipped so several rewards merge - with the reward fee at zero

## Question
Can an unprivileged attacker avoid calling `ping` for many epochs so multiple rewards merge into a single distribution priced at one moment, on a pool whose `reward_fee_fraction` numerator is zero, breaking the invariant that each epoch's reward is distributed at that epoch's share price, and leading to theft of staking rewards or fees owed to other parties?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_ping / ping / on_stake_action`
- Entrypoint: `ping()` - callable by anyone; also reached from `deposit`, `stake`, `unstake`, `withdraw`
- Attacker controls: when the ping happens, how much NEAR is sitting on the account, and whether a stake action is in flight
- Exploit idea: Avoid calling `ping` for many epochs so multiple rewards merge into a single distribution priced at one moment, on a pool whose `reward_fee_fraction` numerator is zero.
- Invariant to test: Each epoch's reward is distributed at that epoch's share price.
- Expected Immunefi impact: High - theft of staking rewards or fees owed to other parties.
- Fast validation: Sim multi-epoch skips and compare against per-epoch pings.
