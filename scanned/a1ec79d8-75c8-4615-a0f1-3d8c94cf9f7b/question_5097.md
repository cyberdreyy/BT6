# Q5097: get_last_globally_accepted_block: signature valid across tenures for the same message

## Question
Can an unprivileged attacker reach `get_last_globally_accepted_block` (in `stacks-signer/src/signerdb.rs`) via a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only), such that the signed hash omits the tenure, breaking the invariant that every signature == bound to one tenure — leading to cross-tenure reuse?

## Target
- File/function: `stacks-signer/src/signerdb.rs` -> `get_last_globally_accepted_block`
- Entrypoint: a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only)
- Attacker controls: the full proposed block contents, its claimed tenure/burn view and reorg claim, and the gossiped signer messages
- Exploit idea: the signed hash omits the tenure
- Invariant to test: every signature == bound to one tenure
- Expected Immunefi impact: Critical - cross-tenure reuse
- Fast validation: test a tenure-agnostic signature
