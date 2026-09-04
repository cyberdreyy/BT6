# Q2998: submit_block_for_validation: aggregate weight rounds up past the threshold

## Question
Can an unprivileged attacker reach `submit_block_for_validation` (in `stacks-signer/src/client/stacks_client.rs`) via a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only), such that `get_signers_weights` threshold math rounds up, breaking the invariant that aggregated weight counted == the true summed distinct weight — leading to finalising below threshold?

## Target
- File/function: `stacks-signer/src/client/stacks_client.rs` -> `submit_block_for_validation`
- Entrypoint: a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only)
- Attacker controls: the full proposed block contents, its claimed tenure/burn view and reorg claim, and the gossiped signer messages
- Exploit idea: `get_signers_weights` threshold math rounds up
- Invariant to test: aggregated weight counted == the true summed distinct weight
- Expected Immunefi impact: Critical - finalising below threshold
- Fast validation: test a rounding boundary
