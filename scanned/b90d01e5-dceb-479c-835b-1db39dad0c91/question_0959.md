# Q0959: insert_update: monitor path leaks a decision that changes signing

## Question
Can an unprivileged attacker reach `insert_update` (in `libsigner/src/v0/signer_state.rs`) via a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only), such that `monitor_signers.rs` feedback alters the decision, breaking the invariant that the decision == a function of validation and canonical view only — leading to decision perturbation?

## Target
- File/function: `libsigner/src/v0/signer_state.rs` -> `insert_update`
- Entrypoint: a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only)
- Attacker controls: the full proposed block contents, its claimed tenure/burn view and reorg claim, and the gossiped signer messages
- Exploit idea: `monitor_signers.rs` feedback alters the decision
- Invariant to test: the decision == a function of validation and canonical view only
- Expected Immunefi impact: High - decision perturbation
- Fast validation: test a monitor feedback loop
