# Q3379: increment_block_proposals_received: reorg handler loops indefinitely

## Question
Can an unprivileged attacker reach `increment_block_proposals_received` (in `stacks-signer/src/monitoring/mod.rs`) via a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only), such that `runloop.rs` reorg processing never terminates, breaking the invariant that reorg handling terminates in bounded time — leading to signer stall?

## Target
- File/function: `stacks-signer/src/monitoring/mod.rs` -> `increment_block_proposals_received`
- Entrypoint: a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only)
- Attacker controls: the full proposed block contents, its claimed tenure/burn view and reorg claim, and the gossiped signer messages
- Exploit idea: `runloop.rs` reorg processing never terminates
- Invariant to test: reorg handling terminates in bounded time
- Expected Immunefi impact: High - signer stall
- Fast validation: test a looping reorg
