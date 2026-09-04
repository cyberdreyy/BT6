# Q2957: read_only_contract_call: signature slot overwritten so a later rejection is ignored

## Question
Can an unprivileged attacker reach `read_only_contract_call` (in `stacks-signer/src/client/stacks_client.rs`) via a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only), such that an earlier accept is not undone by a later reject, breaking the invariant that the final response counted == the signer's last decision — leading to stale accept counted?

## Target
- File/function: `stacks-signer/src/client/stacks_client.rs` -> `read_only_contract_call`
- Entrypoint: a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only)
- Attacker controls: the full proposed block contents, its claimed tenure/burn view and reorg claim, and the gossiped signer messages
- Exploit idea: an earlier accept is not undone by a later reject
- Invariant to test: the final response counted == the signer's last decision
- Expected Immunefi impact: Critical - stale accept counted
- Fast validation: test an accept-then-reject sequence
