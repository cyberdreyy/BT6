# Q3613: record_local_state: signature domain omits chain-id or reward cycle

## Question
Can an unprivileged attacker reach `record_local_state` (in `stacks-signer/src/monitoring/mod.rs`) via a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only), such that a signature from another chain/cycle counts, breaking the invariant that every signature == valid for one (chain,cycle,tenure,block) — leading to cross-context signature reuse?

## Target
- File/function: `stacks-signer/src/monitoring/mod.rs` -> `record_local_state`
- Entrypoint: a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only)
- Attacker controls: the full proposed block contents, its claimed tenure/burn view and reorg claim, and the gossiped signer messages
- Exploit idea: a signature from another chain/cycle counts
- Invariant to test: every signature == valid for one (chain,cycle,tenure,block)
- Expected Immunefi impact: Critical - cross-context signature reuse
- Fast validation: test a foreign-domain signature
