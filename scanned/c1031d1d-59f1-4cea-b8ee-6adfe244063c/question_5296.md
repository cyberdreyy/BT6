# Q5296: has_approved_block_in_tenure: rejection deserializes to acceptance under a lenient parser

## Question
Can an unprivileged attacker reach `has_approved_block_in_tenure` (in `stacks-signer/src/signerdb.rs`) via a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only), such that `v0/messages.rs` parses a rejection as accept, breaking the invariant that bytes of a response == the response type they encode — leading to response confusion?

## Target
- File/function: `stacks-signer/src/signerdb.rs` -> `has_approved_block_in_tenure`
- Entrypoint: a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only)
- Attacker controls: the full proposed block contents, its claimed tenure/burn view and reorg claim, and the gossiped signer messages
- Exploit idea: `v0/messages.rs` parses a rejection as accept
- Invariant to test: bytes of a response == the response type they encode
- Expected Immunefi impact: Critical - response confusion
- Fast validation: test a crafted response
