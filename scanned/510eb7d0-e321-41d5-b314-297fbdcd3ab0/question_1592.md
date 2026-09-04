# Q1592: is_timed_out: validation result cached against the wrong block id

## Question
Can an unprivileged attacker reach `is_timed_out` (in `stacks-signer/src/chainstate/v1.rs`) via a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only), such that a cache hit matches on an insufficient key, breaking the invariant that every block treated as validated == one the node fully validated — leading to signing an unvalidated block?

## Target
- File/function: `stacks-signer/src/chainstate/v1.rs` -> `is_timed_out`
- Entrypoint: a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only)
- Attacker controls: the full proposed block contents, its claimed tenure/burn view and reorg claim, and the gossiped signer messages
- Exploit idea: a cache hit matches on an insufficient key
- Invariant to test: every block treated as validated == one the node fully validated
- Expected Immunefi impact: Critical - signing an unvalidated block
- Fast validation: test a cache-key collision
