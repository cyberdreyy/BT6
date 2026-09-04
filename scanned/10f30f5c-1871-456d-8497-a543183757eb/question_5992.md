# Q5992: prune_superseded_tenures: signer signs for a cycle it is no longer in

## Question
Can an unprivileged attacker reach `prune_superseded_tenures` (in `stacks-signer/src/signerdb.rs`) via a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only), such that cycle membership is checked against stale state, breaking the invariant that the cycles a signer signs for == its actual membership — leading to invalid signature contribution?

## Target
- File/function: `stacks-signer/src/signerdb.rs` -> `prune_superseded_tenures`
- Entrypoint: a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only)
- Attacker controls: the full proposed block contents, its claimed tenure/burn view and reorg claim, and the gossiped signer messages
- Exploit idea: cycle membership is checked against stale state
- Invariant to test: the cycles a signer signs for == its actual membership
- Expected Immunefi impact: High - invalid signature contribution
- Fast validation: test an expired-membership sign
