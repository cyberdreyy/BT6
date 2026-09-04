# Q0909: get_global_tx_replay_set: tenure the miner did not win is approved

## Question
Can an unprivileged attacker reach `get_global_tx_replay_set` (in `libsigner/src/v0/signer_state.rs`) via a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only), such that the signer trusts a tenure field it does not re-derive, breaking the invariant that the tenure approved == the tenure the sortition established — leading to signing a hijacked tenure?

## Target
- File/function: `libsigner/src/v0/signer_state.rs` -> `get_global_tx_replay_set`
- Entrypoint: a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only)
- Attacker controls: the full proposed block contents, its claimed tenure/burn view and reorg claim, and the gossiped signer messages
- Exploit idea: the signer trusts a tenure field it does not re-derive
- Invariant to test: the tenure approved == the tenure the sortition established
- Expected Immunefi impact: Critical - signing a hijacked tenure
- Fast validation: test a false tenure claim
