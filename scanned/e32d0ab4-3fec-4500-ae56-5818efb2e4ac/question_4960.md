# Q4960: get_globally_accepted_block_count_in_tenure: burn/sortition field trusted from the proposal

## Question
Can an unprivileged attacker reach `get_globally_accepted_block_count_in_tenure` (in `stacks-signer/src/signerdb.rs`) via a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only), such that the signer reads sortition data from the miner, breaking the invariant that the burn view judged against == the signer's own node's view — leading to miner-steered signature?

## Target
- File/function: `stacks-signer/src/signerdb.rs` -> `get_globally_accepted_block_count_in_tenure`
- Entrypoint: a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only)
- Attacker controls: the full proposed block contents, its claimed tenure/burn view and reorg claim, and the gossiped signer messages
- Exploit idea: the signer reads sortition data from the miner
- Invariant to test: the burn view judged against == the signer's own node's view
- Expected Immunefi impact: Critical - miner-steered signature
- Fast validation: test a crafted burn field
