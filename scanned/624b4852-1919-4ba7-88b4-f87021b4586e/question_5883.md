# Q5883: Pool proxy makes the pool account the voter - at genesis-like state

## Question
Can an unprivileged attacker vote through a staking pool's `vote` proxy so the recorded voter is the pool account and the stake attribution differs from the key holder's, while `last_epoch_height` is still its initial zero value, breaking the invariant that recorded voters map one-to-one to stake in the validator set, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `voting/src/lib.rs` - `VotingContract::ping / vote / check_result`
- Entrypoint: `ping()` - callable by anyone; `vote(is_vote)` - callable by any account with validator stake, including through a staking pool's `vote` proxy
- Attacker controls: when `ping` is called, the composition of the votes map, and the stake behind each recorded voter
- Exploit idea: Vote through a staking pool's `vote` proxy so the recorded voter is the pool account and the stake attribution differs from the key holder's, while `last_epoch_height` is still its initial zero value.
- Invariant to test: Recorded voters map one-to-one to stake in the validator set.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Sim a proxied vote and inspect the map.
