# Q3745: update_reward_cycle: block info round-trips to a different id on persist

## Question
Can an unprivileged attacker reach `update_reward_cycle` (in `stacks-signer/src/monitoring/mod.rs`) via a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only), such that `signerdb.rs` serialization changes the block id, breaking the invariant that a persisted block id == the original block id — leading to guard bypass via id drift?

## Target
- File/function: `stacks-signer/src/monitoring/mod.rs` -> `update_reward_cycle`
- Entrypoint: a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only)
- Attacker controls: the full proposed block contents, its claimed tenure/burn view and reorg claim, and the gossiped signer messages
- Exploit idea: `signerdb.rs` serialization changes the block id
- Invariant to test: a persisted block id == the original block id
- Expected Immunefi impact: High - guard bypass via id drift
- Fast validation: test a persist round-trip
