# Q2662: Timestamp recorded from the crossing block - repeated votes

## Question
Can an unprivileged attacker influence which block's `env::block_timestamp()` becomes the recorded result, since lockups derive their whole schedule from it, by calling `vote(true)` repeatedly inside one epoch, breaking the invariant that the recorded timestamp is the block in which the supermajority was genuinely reached, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `voting/src/lib.rs` - `VotingContract::ping / vote / check_result`
- Entrypoint: `ping()` - callable by anyone; `vote(is_vote)` - callable by any account with validator stake, including through a staking pool's `vote` proxy
- Attacker controls: when `ping` is called, the composition of the votes map, and the stake behind each recorded voter
- Exploit idea: Influence which block's `env::block_timestamp()` becomes the recorded result, since lockups derive their whole schedule from it, by calling `vote(true)` repeatedly inside one epoch.
- Invariant to test: The recorded timestamp is the block in which the supermajority was genuinely reached.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Unit test the recorded timestamp.
