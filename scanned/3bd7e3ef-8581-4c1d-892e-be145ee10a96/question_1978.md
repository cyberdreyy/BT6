# Q1978: build_read_only_response: aggregate weight rounds up past the threshold

## Question
Can an unprivileged attacker reach `build_read_only_response` (in `stacks-signer/src/client/mod.rs`) via a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only), such that `get_signers_weights` threshold math rounds up, breaking the invariant that aggregated weight counted == the true summed distinct weight — leading to finalising below threshold?

## Target
- File/function: `stacks-signer/src/client/mod.rs` -> `build_read_only_response`
- Entrypoint: a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only)
- Attacker controls: the full proposed block contents, its claimed tenure/burn view and reorg claim, and the gossiped signer messages
- Exploit idea: `get_signers_weights` threshold math rounds up
- Invariant to test: aggregated weight counted == the true summed distinct weight
- Expected Immunefi impact: Critical - finalising below threshold
- Fast validation: test a rounding boundary
