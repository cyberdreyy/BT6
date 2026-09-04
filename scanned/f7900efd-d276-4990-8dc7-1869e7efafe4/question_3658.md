# Q3658: record_signer_agreement_capitulation_latency: aggregate weight rounds up past the threshold

## Question
Can an unprivileged attacker reach `record_signer_agreement_capitulation_latency` (in `stacks-signer/src/monitoring/mod.rs`) via a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only), such that `get_signers_weights` threshold math rounds up, breaking the invariant that aggregated weight counted == the true summed distinct weight — leading to finalising below threshold?

## Target
- File/function: `stacks-signer/src/monitoring/mod.rs` -> `record_signer_agreement_capitulation_latency`
- Entrypoint: a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only)
- Attacker controls: the full proposed block contents, its claimed tenure/burn view and reorg claim, and the gossiped signer messages
- Exploit idea: `get_signers_weights` threshold math rounds up
- Invariant to test: aggregated weight counted == the true summed distinct weight
- Expected Immunefi impact: Critical - finalising below threshold
- Fast validation: test a rounding boundary
