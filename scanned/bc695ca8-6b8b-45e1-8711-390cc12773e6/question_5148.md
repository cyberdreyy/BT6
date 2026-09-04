# Q5148: get_latest_chunk_version: signer wedged into never signing a valid block

## Question
Can an unprivileged attacker reach `get_latest_chunk_version` (in `stacks-signer/src/signerdb.rs`) via a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only), such that a malformed proposal leaves the state machine non-terminal, breaking the invariant that every valid proposal reaches a terminal decision in bounded time — leading to liveness loss?

## Target
- File/function: `stacks-signer/src/signerdb.rs` -> `get_latest_chunk_version`
- Entrypoint: a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only)
- Attacker controls: the full proposed block contents, its claimed tenure/burn view and reorg claim, and the gossiped signer messages
- Exploit idea: a malformed proposal leaves the state machine non-terminal
- Invariant to test: every valid proposal reaches a terminal decision in bounded time
- Expected Immunefi impact: High - liveness loss
- Fast validation: test a wedging proposal
