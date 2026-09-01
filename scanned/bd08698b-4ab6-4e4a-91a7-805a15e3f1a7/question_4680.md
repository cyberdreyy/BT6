# Q4680: Reward attributed to shares bought after it accrued - after a bare donation

## Question
Can an unprivileged attacker delay `ping` until after staking, so a reward earned in an earlier epoch is priced into shares that did not exist during it, after first sending NEAR straight to the pool account with a bare `Transfer` outside any method, breaking the invariant that reward for epoch E accrues only to shares outstanding during E, and leading to theft of staking rewards or fees owed to other parties?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_ping / ping / on_stake_action`
- Entrypoint: `ping()` - callable by anyone; also reached from `deposit`, `stake`, `unstake`, `withdraw`
- Attacker controls: when the ping happens, how much NEAR is sitting on the account, and whether a stake action is in flight
- Exploit idea: Delay `ping` until after staking, so a reward earned in an earlier epoch is priced into shares that did not exist during it, after first sending NEAR straight to the pool account with a bare `Transfer` outside any method.
- Invariant to test: Reward for epoch E accrues only to shares outstanding during E.
- Expected Immunefi impact: High - theft of staking rewards or fees owed to other parties.
- Fast validation: Sim skipped epochs then compare payouts.
