# Q3540: new_rpc_call_timer: client stackerdb read trusts a chunk without owner check

## Question
Can an unprivileged attacker reach `new_rpc_call_timer` (in `stacks-signer/src/monitoring/mod.rs`) via a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only), such that `client/stackerdb.rs` surfaces an unverified chunk to the runloop, breaking the invariant that every chunk acted on == one signed by its slot owner — leading to forged input to the signer?

## Target
- File/function: `stacks-signer/src/monitoring/mod.rs` -> `new_rpc_call_timer`
- Entrypoint: a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only)
- Attacker controls: the full proposed block contents, its claimed tenure/burn view and reorg claim, and the gossiped signer messages
- Exploit idea: `client/stackerdb.rs` surfaces an unverified chunk to the runloop
- Invariant to test: every chunk acted on == one signed by its slot owner
- Expected Immunefi impact: Critical - forged input to the signer
- Fast validation: test an unverified chunk read
