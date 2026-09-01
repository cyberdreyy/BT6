# Q5806: Vote withdrawal underflows the total - at genesis-like state

## Question
Can an unprivileged attacker call `vote(false)` in a sequence where `total_voted_stake + account_stake - voted_stake` underflows or mis-nets, while `last_epoch_height` is still its initial zero value, breaking the invariant that `total_voted_stake` never underflows and always equals the sum of live votes, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `voting/src/lib.rs` - `VotingContract::ping / vote / check_result`
- Entrypoint: `ping()` - callable by anyone; `vote(is_vote)` - callable by any account with validator stake, including through a staking pool's `vote` proxy
- Attacker controls: when `ping` is called, the composition of the votes map, and the stake behind each recorded voter
- Exploit idea: Call `vote(false)` in a sequence where `total_voted_stake + account_stake - voted_stake` underflows or mis-nets, while `last_epoch_height` is still its initial zero value.
- Invariant to test: `total_voted_stake` never underflows and always equals the sum of live votes.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Unit test the arithmetic across vote/withdraw sequences.
