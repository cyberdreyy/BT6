# Q3822: Total counted while the voter is dropped - stale votes map

## Question
Can an unprivileged attacker exploit `ping` adding `account_current_stake` to `total_voted_stake` before deciding whether to keep the voter in `votes`, so the totals and the map disagree, while `votes` still holds entries for validators that left the set, breaking the invariant that `total_voted_stake == sum(votes.values())` after every call, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `voting/src/lib.rs` - `VotingContract::ping / vote / check_result`
- Entrypoint: `ping()` - callable by anyone; `vote(is_vote)` - callable by any account with validator stake, including through a staking pool's `vote` proxy
- Attacker controls: when `ping` is called, the composition of the votes map, and the stake behind each recorded voter
- Exploit idea: Exploit `ping` adding `account_current_stake` to `total_voted_stake` before deciding whether to keep the voter in `votes`, so the totals and the map disagree, while `votes` still holds entries for validators that left the set.
- Invariant to test: `total_voted_stake == sum(votes.values())` after every call.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Unit test the ping loop and compare both.
