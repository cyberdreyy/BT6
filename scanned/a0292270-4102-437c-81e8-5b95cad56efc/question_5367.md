# Q5367: Votes map inflated with zero-stake entries - with a lockup waiting

## Question
Can an unprivileged attacker keep entries in `votes` for accounts whose stake is zero so later pings mis-derive the total, while a lockup contract is waiting to read the result, breaking the invariant that the map holds only accounts with positive live stake, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `voting/src/lib.rs` - `VotingContract::ping / vote / check_result`
- Entrypoint: `ping()` - callable by anyone; `vote(is_vote)` - callable by any account with validator stake, including through a staking pool's `vote` proxy
- Attacker controls: when `ping` is called, the composition of the votes map, and the stake behind each recorded voter
- Exploit idea: Keep entries in `votes` for accounts whose stake is zero so later pings mis-derive the total, while a lockup contract is waiting to read the result.
- Invariant to test: The map holds only accounts with positive live stake.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Unit test the ping loop's filtering.
