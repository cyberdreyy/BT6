# Q4398: Result latched from a stale votes map - ping and vote same block

## Question
Can an unprivileged attacker let `total_voted_stake` be recomputed in `ping` from validators whose stake changed, so `check_result` latches `result` on a figure that no longer holds, with `ping` and `vote` in the same block, `last_epoch_height` already current, breaking the invariant that `result` is set only when live voted stake truly exceeds two thirds of live total stake, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `voting/src/lib.rs` - `VotingContract::ping / vote / check_result`
- Entrypoint: `ping()` - callable by anyone; `vote(is_vote)` - callable by any account with validator stake, including through a staking pool's `vote` proxy
- Attacker controls: when `ping` is called, the composition of the votes map, and the stake behind each recorded voter
- Exploit idea: Let `total_voted_stake` be recomputed in `ping` from validators whose stake changed, so `check_result` latches `result` on a figure that no longer holds, with `ping` and `vote` in the same block, `last_epoch_height` already current.
- Invariant to test: `result` is set only when live voted stake truly exceeds two thirds of live total stake.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Unit test ping across a stake change and inspect `result`.
