# Q3002: try_from_host: validation result cached against the wrong block id

## Question
Can an unprivileged attacker reach `try_from_host` (in `stacks-signer/src/client/stacks_client.rs`) via a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only), such that a cache hit matches on an insufficient key, breaking the invariant that every block treated as validated == one the node fully validated — leading to signing an unvalidated block?

## Target
- File/function: `stacks-signer/src/client/stacks_client.rs` -> `try_from_host`
- Entrypoint: a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only)
- Attacker controls: the full proposed block contents, its claimed tenure/burn view and reorg claim, and the gossiped signer messages
- Exploit idea: a cache hit matches on an insufficient key
- Invariant to test: every block treated as validated == one the node fully validated
- Expected Immunefi impact: Critical - signing an unvalidated block
- Fast validation: test a cache-key collision
