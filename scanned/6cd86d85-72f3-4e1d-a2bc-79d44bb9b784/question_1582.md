# Q1582: fetch_view: signer signs for a cycle it is no longer in

## Question
Can an unprivileged attacker reach `fetch_view` (in `stacks-signer/src/chainstate/v1.rs`) via a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only), such that cycle membership is checked against stale state, breaking the invariant that the cycles a signer signs for == its actual membership — leading to invalid signature contribution?

## Target
- File/function: `stacks-signer/src/chainstate/v1.rs` -> `fetch_view`
- Entrypoint: a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only)
- Attacker controls: the full proposed block contents, its claimed tenure/burn view and reorg claim, and the gossiped signer messages
- Exploit idea: cycle membership is checked against stale state
- Invariant to test: the cycles a signer signs for == its actual membership
- Expected Immunefi impact: High - invalid signature contribution
- Fast validation: test an expired-membership sign
