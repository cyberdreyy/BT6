# Q1118: reached_agreement: reorg deeper than the rules permit is accepted

## Question
Can an unprivileged attacker reach `reached_agreement` (in `libsigner/src/v0/signer_state.rs`) via a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only), such that `chainstate/v2.rs` depth/time rule satisfied by a stalled burn view, breaking the invariant that the reorg approved <= the allowed depth — leading to deep-fork signature?

## Target
- File/function: `libsigner/src/v0/signer_state.rs` -> `reached_agreement`
- Entrypoint: a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only)
- Attacker controls: the full proposed block contents, its claimed tenure/burn view and reorg claim, and the gossiped signer messages
- Exploit idea: `chainstate/v2.rs` depth/time rule satisfied by a stalled burn view
- Invariant to test: the reorg approved <= the allowed depth
- Expected Immunefi impact: Critical - deep-fork signature
- Fast validation: test an over-deep reorg
