# Q5795: Threshold crossed by a shrinking denominator - at genesis-like state

## Question
Can an unprivileged attacker reach the two-thirds condition because `env::validator_total_stake()` collapsed rather than because voters supported it, while `last_epoch_height` is still its initial zero value, breaking the invariant that the result reflects genuine supermajority support, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `voting/src/lib.rs` - `VotingContract::ping / vote / check_result`
- Entrypoint: `ping()` - callable by anyone; `vote(is_vote)` - callable by any account with validator stake, including through a staking pool's `vote` proxy
- Attacker controls: when `ping` is called, the composition of the votes map, and the stake behind each recorded voter
- Exploit idea: Reach the two-thirds condition because `env::validator_total_stake()` collapsed rather than because voters supported it, while `last_epoch_height` is still its initial zero value.
- Invariant to test: The result reflects genuine supermajority support.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Unit test with a shrinking total stake.
