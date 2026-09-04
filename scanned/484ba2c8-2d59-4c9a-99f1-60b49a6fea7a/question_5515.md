# Q5515: insert_block_validated_by_replay_tx: block info round-trips to a different id on persist

## Question
Can an unprivileged attacker reach `insert_block_validated_by_replay_tx` (in `stacks-signer/src/signerdb.rs`) via a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only), such that `signerdb.rs` serialization changes the block id, breaking the invariant that a persisted block id == the original block id — leading to guard bypass via id drift?

## Target
- File/function: `stacks-signer/src/signerdb.rs` -> `insert_block_validated_by_replay_tx`
- Entrypoint: a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only)
- Attacker controls: the full proposed block contents, its claimed tenure/burn view and reorg claim, and the gossiped signer messages
- Exploit idea: `signerdb.rs` serialization changes the block id
- Invariant to test: a persisted block id == the original block id
- Expected Immunefi impact: High - guard bypass via id drift
- Fast validation: test a persist round-trip
