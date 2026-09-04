# Q4692: get_block_signatures: fault-injection stall hook reachable in a release build

## Question
Can an unprivileged attacker reach `get_block_signatures` (in `stacks-signer/src/signerdb.rs`) via a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only), such that `fault_injection_validation_stall` is not compiled out, breaking the invariant that validation behaviour in release == full validation only — leading to forced accept path?

## Target
- File/function: `stacks-signer/src/signerdb.rs` -> `get_block_signatures`
- Entrypoint: a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only)
- Attacker controls: the full proposed block contents, its claimed tenure/burn view and reorg claim, and the gossiped signer messages
- Exploit idea: `fault_injection_validation_stall` is not compiled out
- Invariant to test: validation behaviour in release == full validation only
- Expected Immunefi impact: Critical - forced accept path
- Fast validation: inspect/test the release build path
