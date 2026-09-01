# Q4470: Epoch skipped so several rewards merge - attacker holds most shares

## Question
Can an unprivileged attacker avoid calling `ping` for many epochs so multiple rewards merge into a single distribution priced at one moment, while holding the majority of `total_stake_shares`, so rounding accrues mostly to the attacker, breaking the invariant that each epoch's reward is distributed at that epoch's share price, and leading to theft of staking rewards or fees owed to other parties?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_ping / ping / on_stake_action`
- Entrypoint: `ping()` - callable by anyone; also reached from `deposit`, `stake`, `unstake`, `withdraw`
- Attacker controls: when the ping happens, how much NEAR is sitting on the account, and whether a stake action is in flight
- Exploit idea: Avoid calling `ping` for many epochs so multiple rewards merge into a single distribution priced at one moment, while holding the majority of `total_stake_shares`, so rounding accrues mostly to the attacker.
- Invariant to test: Each epoch's reward is distributed at that epoch's share price.
- Expected Immunefi impact: High - theft of staking rewards or fees owed to other parties.
- Fast validation: Sim multi-epoch skips and compare against per-epoch pings.
