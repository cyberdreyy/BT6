# Q5070: Last_epoch_height set before distribution completes - paused pool

## Question
Can an unprivileged attacker abort the reward path after `last_epoch_height` was advanced, so the epoch can never be pinged again, while `paused == true`, so `internal_restake` returns early and nothing is re-staked, breaking the invariant that an epoch's reward is distributed exactly once and never lost, and leading to theft of staking rewards or fees owed to other parties?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_ping / ping / on_stake_action`
- Entrypoint: `ping()` - callable by anyone; also reached from `deposit`, `stake`, `unstake`, `withdraw`
- Attacker controls: when the ping happens, how much NEAR is sitting on the account, and whether a stake action is in flight
- Exploit idea: Abort the reward path after `last_epoch_height` was advanced, so the epoch can never be pinged again, while `paused == true`, so `internal_restake` returns early and nothing is re-staked.
- Invariant to test: An epoch's reward is distributed exactly once and never lost.
- Expected Immunefi impact: High - theft of staking rewards or fees owed to other parties.
- Fast validation: Sim a partial failure and assert the reward still lands.
