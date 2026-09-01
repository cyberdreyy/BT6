# Q4827: Check_result called twice in one call - read by a lockup

## Question
Can an unprivileged attacker reach `check_result` a second time inside one call so its own assertion fires and reverts a legitimate vote, at the moment a lockup's `check_transfers_vote` reads `get_result`, breaking the invariant that `check_result` runs at most once per state transition, and leading to protocol accounting value diverges from reality and another party settles on it?

## Target
- File/function: `voting/src/lib.rs` - `VotingContract::ping / vote / check_result`
- Entrypoint: `ping()` - callable by anyone; `vote(is_vote)` - callable by any account with validator stake, including through a staking pool's `vote` proxy
- Attacker controls: when `ping` is called, the composition of the votes map, and the stake behind each recorded voter
- Exploit idea: Reach `check_result` a second time inside one call so its own assertion fires and reverts a legitimate vote, at the moment a lockup's `check_transfers_vote` reads `get_result`.
- Invariant to test: `check_result` runs at most once per state transition.
- Expected Immunefi impact: High - protocol accounting value diverges from reality and another party settles on it.
- Fast validation: Unit test a vote that crosses the threshold.
