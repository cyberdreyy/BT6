# Q1643: reset_view: slot index maps a response to another signer

## Question
Can an unprivileged attacker reach `reset_view` (in `stacks-signer/src/chainstate/v1.rs`) via a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only), such that a wrong slot index misattributes a signature, breaking the invariant that the signer a response is credited to == its signer — leading to vote misattribution?

## Target
- File/function: `stacks-signer/src/chainstate/v1.rs` -> `reset_view`
- Entrypoint: a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only)
- Attacker controls: the full proposed block contents, its claimed tenure/burn view and reorg claim, and the gossiped signer messages
- Exploit idea: a wrong slot index misattributes a signature
- Invariant to test: the signer a response is credited to == its signer
- Expected Immunefi impact: High - vote misattribution
- Fast validation: test a wrong slot index
