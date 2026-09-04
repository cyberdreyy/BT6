# Q3818: update_stacks_tip_height: reorg deeper than the rules permit is accepted

## Question
Can an unprivileged attacker reach `update_stacks_tip_height` (in `stacks-signer/src/monitoring/mod.rs`) via a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only), such that `chainstate/v2.rs` depth/time rule satisfied by a stalled burn view, breaking the invariant that the reorg approved <= the allowed depth — leading to deep-fork signature?

## Target
- File/function: `stacks-signer/src/monitoring/mod.rs` -> `update_stacks_tip_height`
- Entrypoint: a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only)
- Attacker controls: the full proposed block contents, its claimed tenure/burn view and reorg claim, and the gossiped signer messages
- Exploit idea: `chainstate/v2.rs` depth/time rule satisfied by a stalled burn view
- Invariant to test: the reorg approved <= the allowed depth
- Expected Immunefi impact: Critical - deep-fork signature
- Fast validation: test an over-deep reorg
