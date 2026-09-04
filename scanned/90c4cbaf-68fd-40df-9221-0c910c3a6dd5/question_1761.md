# Q1761: parse_pox_addr: signer acts on a stale reward set

## Question
Can an unprivileged attacker reach `parse_pox_addr` (in `stacks-signer/src/cli.rs`) via a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only), such that `libsigner/signer_set.rs` uses an outdated set, breaking the invariant that the reward set/index/threshold used == consensus's for that cycle — leading to miscounted weight?

## Target
- File/function: `stacks-signer/src/cli.rs` -> `parse_pox_addr`
- Entrypoint: a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only)
- Attacker controls: the full proposed block contents, its claimed tenure/burn view and reorg claim, and the gossiped signer messages
- Exploit idea: `libsigner/signer_set.rs` uses an outdated set
- Invariant to test: the reward set/index/threshold used == consensus's for that cycle
- Expected Immunefi impact: High - miscounted weight
- Fast validation: test a stale set
