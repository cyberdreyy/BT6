# Q3764: update_signer_nonce: a rejection message replays as an acceptance

## Question
Can an unprivileged attacker reach `update_signer_nonce` (in `stacks-signer/src/monitoring/mod.rs`) via a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only), such that the message type is not bound into the signed hash, breaking the invariant that a signed response's meaning == the type the signer intended — leading to rejection counted as accept?

## Target
- File/function: `stacks-signer/src/monitoring/mod.rs` -> `update_signer_nonce`
- Entrypoint: a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only)
- Attacker controls: the full proposed block contents, its claimed tenure/burn view and reorg claim, and the gossiped signer messages
- Exploit idea: the message type is not bound into the signed hash
- Invariant to test: a signed response's meaning == the type the signer intended
- Expected Immunefi impact: Critical - rejection counted as accept
- Fast validation: test a replayed rejection
