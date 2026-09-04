# Q3380: increment_block_proposals_received: signerdb write failure treated as success

## Question
Can an unprivileged attacker reach `increment_block_proposals_received` (in `stacks-signer/src/monitoring/mod.rs`) via a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only), such that a failed persist is not detected, so the signer waits forever, breaking the invariant that a persisted decision == an acknowledged durable write — leading to liveness / re-sign?

## Target
- File/function: `stacks-signer/src/monitoring/mod.rs` -> `increment_block_proposals_received`
- Entrypoint: a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only)
- Attacker controls: the full proposed block contents, its claimed tenure/burn view and reorg claim, and the gossiped signer messages
- Exploit idea: a failed persist is not detected, so the signer waits forever
- Invariant to test: a persisted decision == an acknowledged durable write
- Expected Immunefi impact: High - liveness / re-sign
- Fast validation: test a failing signerdb write
