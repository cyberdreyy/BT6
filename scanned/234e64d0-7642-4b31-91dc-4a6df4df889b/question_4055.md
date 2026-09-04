# Q4055: not_registered: reorg claim resets signer state and enables a competing signature

## Question
Can an unprivileged attacker reach `not_registered` (in `stacks-signer/src/runloop.rs`) via a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only), such that `chainstate/v2.rs` reorg handling clears the equivocation guard, breaking the invariant that a reorg never permits signing a second block at a decided height — leading to equivocation via reorg?

## Target
- File/function: `stacks-signer/src/runloop.rs` -> `not_registered`
- Entrypoint: a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only)
- Attacker controls: the full proposed block contents, its claimed tenure/burn view and reorg claim, and the gossiped signer messages
- Exploit idea: `chainstate/v2.rs` reorg handling clears the equivocation guard
- Invariant to test: a reorg never permits signing a second block at a decided height
- Expected Immunefi impact: Critical - equivocation via reorg
- Fast validation: test a crafted reorg claim
