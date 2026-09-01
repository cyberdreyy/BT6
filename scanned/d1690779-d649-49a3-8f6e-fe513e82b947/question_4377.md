# Q4377: Stake read for a non-validator - many tiny voters

## Question
Can an unprivileged attacker call `vote(true)` from an account whose `env::validator_stake` is non-zero only transiently, with a large number of accounts each carrying a tiny recorded stake, breaking the invariant that recorded stake reflects the validator set at the recorded epoch, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `voting/src/lib.rs` - `VotingContract::ping / vote / check_result`
- Entrypoint: `ping()` - callable by anyone; `vote(is_vote)` - callable by any account with validator stake, including through a staking pool's `vote` proxy
- Attacker controls: when `ping` is called, the composition of the votes map, and the stake behind each recorded voter
- Exploit idea: Call `vote(true)` from an account whose `env::validator_stake` is non-zero only transiently, with a large number of accounts each carrying a tiny recorded stake.
- Invariant to test: Recorded stake reflects the validator set at the recorded epoch.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Unit test around a set change.
