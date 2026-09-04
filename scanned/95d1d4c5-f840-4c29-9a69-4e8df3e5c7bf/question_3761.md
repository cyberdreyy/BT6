# Q3761: update_signer_nonce: proposal validation bypassed when auth_token absent

## Question
Can an unprivileged attacker reach `update_signer_nonce` (in `stacks-signer/src/monitoring/mod.rs`) via a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only), such that `postblock_proposal.rs` treats missing token as allow, breaking the invariant that every validated block == one the endpoint actually ran validation on — leading to unvalidated block signed?

## Target
- File/function: `stacks-signer/src/monitoring/mod.rs` -> `update_signer_nonce`
- Entrypoint: a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only)
- Attacker controls: the full proposed block contents, its claimed tenure/burn view and reorg claim, and the gossiped signer messages
- Exploit idea: `postblock_proposal.rs` treats missing token as allow
- Invariant to test: every validated block == one the endpoint actually ran validation on
- Expected Immunefi impact: Critical - unvalidated block signed
- Fast validation: test a missing auth_token path
