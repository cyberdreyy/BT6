# Q5936: move_to: burn-height cursor read stale so validation uses an old view

## Question
Can an unprivileged attacker reach `move_to` (in `stacks-signer/src/signerdb.rs`) via a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only), such that the signer validates against an outdated burn tip, breaking the invariant that the burn view validated against == the current canonical tip — leading to stale-view signature?

## Target
- File/function: `stacks-signer/src/signerdb.rs` -> `move_to`
- Entrypoint: a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only)
- Attacker controls: the full proposed block contents, its claimed tenure/burn view and reorg claim, and the gossiped signer messages
- Exploit idea: the signer validates against an outdated burn tip
- Invariant to test: the burn view validated against == the current canonical tip
- Expected Immunefi impact: Critical - stale-view signature
- Fast validation: test a stale cursor
