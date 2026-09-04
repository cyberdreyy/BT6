# Q0756: version: restart loses the last-signed record

## Question
Can an unprivileged attacker reach `version` (in `libsigner/src/v0/messages.rs`) via a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only), such that `signerdb.rs` read-back drops the equivocation guard, breaking the invariant that decisions after restart == decisions before it — leading to re-sign after restart?

## Target
- File/function: `libsigner/src/v0/messages.rs` -> `version`
- Entrypoint: a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only)
- Attacker controls: the full proposed block contents, its claimed tenure/burn view and reorg claim, and the gossiped signer messages
- Exploit idea: `signerdb.rs` read-back drops the equivocation guard
- Invariant to test: decisions after restart == decisions before it
- Expected Immunefi impact: High - re-sign after restart
- Fast validation: test a restart round-trip
