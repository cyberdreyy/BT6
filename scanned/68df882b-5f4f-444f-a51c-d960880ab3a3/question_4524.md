# Q4524: Ping refused after the result is set - ping and vote same block

## Question
Can an unprivileged attacker use the `assert!(self.result.is_none())` in `ping` so the contract panics for every later caller, breaking callers that chain on it, with `ping` and `vote` in the same block, `last_epoch_height` already current, breaking the invariant that a completed poll never aborts an honest caller's promise chain, and leading to protocol accounting value diverges from reality and another party settles on it?

## Target
- File/function: `voting/src/lib.rs` - `VotingContract::ping / vote / check_result`
- Entrypoint: `ping()` - callable by anyone; `vote(is_vote)` - callable by any account with validator stake, including through a staking pool's `vote` proxy
- Attacker controls: when `ping` is called, the composition of the votes map, and the stake behind each recorded voter
- Exploit idea: Use the `assert!(self.result.is_none())` in `ping` so the contract panics for every later caller, breaking callers that chain on it, with `ping` and `vote` in the same block, `last_epoch_height` already current.
- Invariant to test: A completed poll never aborts an honest caller's promise chain.
- Expected Immunefi impact: High - protocol accounting value diverges from reality and another party settles on it.
- Fast validation: Sim a chained caller after the result is set.
