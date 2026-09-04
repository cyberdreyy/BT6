# Q2955: read_only_contract_call: aggregation counts an accept for block A toward block B

## Question
Can an unprivileged attacker reach `read_only_contract_call` (in `stacks-signer/src/client/stacks_client.rs`) via a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only), such that two blocks share a response hash, breaking the invariant that weight aggregated for a block == accepts verifying over that block's hash — leading to false finalisation?

## Target
- File/function: `stacks-signer/src/client/stacks_client.rs` -> `read_only_contract_call`
- Entrypoint: a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only)
- Attacker controls: the full proposed block contents, its claimed tenure/burn view and reorg claim, and the gossiped signer messages
- Exploit idea: two blocks share a response hash
- Invariant to test: weight aggregated for a block == accepts verifying over that block's hash
- Expected Immunefi impact: Critical - false finalisation
- Fast validation: test a shared-hash response
