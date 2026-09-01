# Q3070: Last_epoch_height advanced without recomputation - result already set

## Question
Can an unprivileged attacker reach a path where `last_epoch_height` is updated but the recomputation it guards did not fully run, after `result` is already `Some`, when `ping` and `vote` take their early-return paths, breaking the invariant that the epoch marker only advances after a complete recomputation, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `voting/src/lib.rs` - `VotingContract::ping / vote / check_result`
- Entrypoint: `ping()` - callable by anyone; `vote(is_vote)` - callable by any account with validator stake, including through a staking pool's `vote` proxy
- Attacker controls: when `ping` is called, the composition of the votes map, and the stake behind each recorded voter
- Exploit idea: Reach a path where `last_epoch_height` is updated but the recomputation it guards did not fully run, after `result` is already `Some`, when `ping` and `vote` take their early-return paths.
- Invariant to test: The epoch marker only advances after a complete recomputation.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Unit test the ping body's ordering.
