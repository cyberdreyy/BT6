# Q2468: get_current_and_last_sortition: reorg deeper than the rules permit is accepted

## Question
Can an unprivileged attacker reach `get_current_and_last_sortition` (in `stacks-signer/src/client/stacks_client.rs`) via a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only), such that `chainstate/v2.rs` depth/time rule satisfied by a stalled burn view, breaking the invariant that the reorg approved <= the allowed depth — leading to deep-fork signature?

## Target
- File/function: `stacks-signer/src/client/stacks_client.rs` -> `get_current_and_last_sortition`
- Entrypoint: a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only)
- Attacker controls: the full proposed block contents, its claimed tenure/burn view and reorg claim, and the gossiped signer messages
- Exploit idea: `chainstate/v2.rs` depth/time rule satisfied by a stalled burn view
- Invariant to test: the reorg approved <= the allowed depth
- Expected Immunefi impact: Critical - deep-fork signature
- Fast validation: test an over-deep reorg
