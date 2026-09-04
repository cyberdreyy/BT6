# Q1398: is_tenure_valid: signer wedged into never signing a valid block

## Question
Can an unprivileged attacker reach `is_tenure_valid` (in `stacks-signer/src/chainstate/mod.rs`) via a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only), such that a malformed proposal leaves the state machine non-terminal, breaking the invariant that every valid proposal reaches a terminal decision in bounded time — leading to liveness loss?

## Target
- File/function: `stacks-signer/src/chainstate/mod.rs` -> `is_tenure_valid`
- Entrypoint: a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only)
- Attacker controls: the full proposed block contents, its claimed tenure/burn view and reorg claim, and the gossiped signer messages
- Exploit idea: a malformed proposal leaves the state machine non-terminal
- Invariant to test: every valid proposal reaches a terminal decision in bounded time
- Expected Immunefi impact: High - liveness loss
- Fast validation: test a wedging proposal
