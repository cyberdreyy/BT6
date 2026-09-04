# Q5504: insert_block_validated_by_replay_tx: a rejection message replays as an acceptance

## Question
Can an unprivileged attacker reach `insert_block_validated_by_replay_tx` (in `stacks-signer/src/signerdb.rs`) via a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only), such that the message type is not bound into the signed hash, breaking the invariant that a signed response's meaning == the type the signer intended — leading to rejection counted as accept?

## Target
- File/function: `stacks-signer/src/signerdb.rs` -> `insert_block_validated_by_replay_tx`
- Entrypoint: a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only)
- Attacker controls: the full proposed block contents, its claimed tenure/burn view and reorg claim, and the gossiped signer messages
- Exploit idea: the message type is not bound into the signed hash
- Invariant to test: a signed response's meaning == the type the signer intended
- Expected Immunefi impact: Critical - rejection counted as accept
- Fast validation: test a replayed rejection
