# Q4009: is_in_next_prepare_phase: reorg handler loops indefinitely

## Question
Can an unprivileged attacker reach `is_in_next_prepare_phase` (in `stacks-signer/src/runloop.rs`) via a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only), such that `runloop.rs` reorg processing never terminates, breaking the invariant that reorg handling terminates in bounded time — leading to signer stall?

## Target
- File/function: `stacks-signer/src/runloop.rs` -> `is_in_next_prepare_phase`
- Entrypoint: a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only)
- Attacker controls: the full proposed block contents, its claimed tenure/burn view and reorg claim, and the gossiped signer messages
- Exploit idea: `runloop.rs` reorg processing never terminates
- Invariant to test: reorg handling terminates in bounded time
- Expected Immunefi impact: High - signer stall
- Fast validation: test a looping reorg
