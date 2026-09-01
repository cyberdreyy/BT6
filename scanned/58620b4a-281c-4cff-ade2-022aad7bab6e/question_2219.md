# Q2219: Double counting across epochs - vote withdrawn

## Question
Can an unprivileged attacker vote in one epoch and let `ping` in the next re-add the same stake without removing the old entry, by calling `vote(false)` to withdraw a previously counted vote, breaking the invariant that each voter contributes its stake exactly once, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `voting/src/lib.rs` - `VotingContract::ping / vote / check_result`
- Entrypoint: `ping()` - callable by anyone; `vote(is_vote)` - callable by any account with validator stake, including through a staking pool's `vote` proxy
- Attacker controls: when `ping` is called, the composition of the votes map, and the stake behind each recorded voter
- Exploit idea: Vote in one epoch and let `ping` in the next re-add the same stake without removing the old entry, by calling `vote(false)` to withdraw a previously counted vote.
- Invariant to test: Each voter contributes its stake exactly once.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Sim two epochs of voting and compare totals.
