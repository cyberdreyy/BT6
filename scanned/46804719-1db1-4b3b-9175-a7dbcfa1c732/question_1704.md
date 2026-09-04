# Q1704: check_proposal: proposal height/tenure read from the miner not the canonical view

## Question
Can an unprivileged attacker reach `check_proposal` (in `stacks-signer/src/chainstate/v2.rs`) via a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only), such that the equivocation check keys on miner-supplied height, breaking the invariant that the height a decision is keyed on == the canonical height — leading to equivocation via mislabeled height?

## Target
- File/function: `stacks-signer/src/chainstate/v2.rs` -> `check_proposal`
- Entrypoint: a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only)
- Attacker controls: the full proposed block contents, its claimed tenure/burn view and reorg claim, and the gossiped signer messages
- Exploit idea: the equivocation check keys on miner-supplied height
- Invariant to test: the height a decision is keyed on == the canonical height
- Expected Immunefi impact: Critical - equivocation via mislabeled height
- Fast validation: test a mislabeled proposal
