# Q1019: Vote accepted after the poll ended - voter stake grew

## Question
Can an unprivileged attacker have `vote` return early on a set result while still having mutated state, for an account whose stake grew sharply after it voted, breaking the invariant that no state changes once the result is final, and leading to protocol accounting value diverges from reality and another party settles on it?

## Target
- File/function: `voting/src/lib.rs` - `VotingContract::ping / vote / check_result`
- Entrypoint: `ping()` - callable by anyone; `vote(is_vote)` - callable by any account with validator stake, including through a staking pool's `vote` proxy
- Attacker controls: when `ping` is called, the composition of the votes map, and the stake behind each recorded voter
- Exploit idea: Have `vote` return early on a set result while still having mutated state, for an account whose stake grew sharply after it voted.
- Invariant to test: No state changes once the result is final.
- Expected Immunefi impact: High - protocol accounting value diverges from reality and another party settles on it.
- Fast validation: Unit test a vote after the result is set.
