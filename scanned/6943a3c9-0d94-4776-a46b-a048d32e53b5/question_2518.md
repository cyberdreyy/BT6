# Q2518: Threshold crossed by a shrinking denominator - repeated votes

## Question
Can an unprivileged attacker reach the two-thirds condition because `env::validator_total_stake()` collapsed rather than because voters supported it, by calling `vote(true)` repeatedly inside one epoch, breaking the invariant that the result reflects genuine supermajority support, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `voting/src/lib.rs` - `VotingContract::ping / vote / check_result`
- Entrypoint: `ping()` - callable by anyone; `vote(is_vote)` - callable by any account with validator stake, including through a staking pool's `vote` proxy
- Attacker controls: when `ping` is called, the composition of the votes map, and the stake behind each recorded voter
- Exploit idea: Reach the two-thirds condition because `env::validator_total_stake()` collapsed rather than because voters supported it, by calling `vote(true)` repeatedly inside one epoch.
- Invariant to test: The result reflects genuine supermajority support.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Unit test with a shrinking total stake.
