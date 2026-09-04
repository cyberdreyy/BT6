# Q5634: insert_state_machine_update: proposal height/tenure read from the miner not the canonical view

## Question
Can an unprivileged attacker reach `insert_state_machine_update` (in `stacks-signer/src/signerdb.rs`) via a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only), such that the equivocation check keys on miner-supplied height, breaking the invariant that the height a decision is keyed on == the canonical height — leading to equivocation via mislabeled height?

## Target
- File/function: `stacks-signer/src/signerdb.rs` -> `insert_state_machine_update`
- Entrypoint: a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only)
- Attacker controls: the full proposed block contents, its claimed tenure/burn view and reorg claim, and the gossiped signer messages
- Exploit idea: the equivocation check keys on miner-supplied height
- Invariant to test: the height a decision is keyed on == the canonical height
- Expected Immunefi impact: Critical - equivocation via mislabeled height
- Fast validation: test a mislabeled proposal
