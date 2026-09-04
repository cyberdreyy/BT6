# Q4264: add_pending_block_signature_response: signer signs two conflicting blocks at one height

## Question
Can an unprivileged attacker reach `add_pending_block_signature_response` (in `stacks-signer/src/signerdb.rs`) via a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only), such that the signerdb equivocation key omits a distinguishing field, breaking the invariant that distinct blocks signed per (cycle,tenure,height) == at most one — leading to equivocation (chain safety)?

## Target
- File/function: `stacks-signer/src/signerdb.rs` -> `add_pending_block_signature_response`
- Entrypoint: a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only)
- Attacker controls: the full proposed block contents, its claimed tenure/burn view and reorg claim, and the gossiped signer messages
- Exploit idea: the signerdb equivocation key omits a distinguishing field
- Invariant to test: distinct blocks signed per (cycle,tenure,height) == at most one
- Expected Immunefi impact: Critical - equivocation (chain safety)
- Fast validation: test two blocks at one height
