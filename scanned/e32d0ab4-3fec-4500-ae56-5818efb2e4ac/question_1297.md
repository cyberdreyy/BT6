# Q1297: data: signer accepts a block on a non-canonical parent

## Question
Can an unprivileged attacker reach `data` (in `stacks-signer/src/chainstate/mod.rs`) via a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only), such that `chainstate/v1.rs` trusts a parent from the proposal, breaking the invariant that the parent approved == the canonical sortition's parent — leading to signing a non-canonical block?

## Target
- File/function: `stacks-signer/src/chainstate/mod.rs` -> `data`
- Entrypoint: a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only)
- Attacker controls: the full proposed block contents, its claimed tenure/burn view and reorg claim, and the gossiped signer messages
- Exploit idea: `chainstate/v1.rs` trusts a parent from the proposal
- Invariant to test: the parent approved == the canonical sortition's parent
- Expected Immunefi impact: Critical - signing a non-canonical block
- Fast validation: test a non-canonical parent proposal
