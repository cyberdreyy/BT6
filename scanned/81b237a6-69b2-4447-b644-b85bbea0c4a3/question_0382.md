# Q0382: inner_consensus_serialize: signer signs for a cycle it is no longer in

## Question
Can an unprivileged attacker reach `inner_consensus_serialize` (in `libsigner/src/v0/messages.rs`) via a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only), such that cycle membership is checked against stale state, breaking the invariant that the cycles a signer signs for == its actual membership — leading to invalid signature contribution?

## Target
- File/function: `libsigner/src/v0/messages.rs` -> `inner_consensus_serialize`
- Entrypoint: a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only)
- Attacker controls: the full proposed block contents, its claimed tenure/burn view and reorg claim, and the gossiped signer messages
- Exploit idea: cycle membership is checked against stale state
- Invariant to test: the cycles a signer signs for == its actual membership
- Expected Immunefi impact: High - invalid signature contribution
- Fast validation: test an expired-membership sign
