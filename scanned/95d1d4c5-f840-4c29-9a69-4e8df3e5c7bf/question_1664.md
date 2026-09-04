# Q1664: state: a rejection message replays as an acceptance

## Question
Can an unprivileged attacker reach `state` (in `stacks-signer/src/chainstate/v1.rs`) via a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only), such that the message type is not bound into the signed hash, breaking the invariant that a signed response's meaning == the type the signer intended — leading to rejection counted as accept?

## Target
- File/function: `stacks-signer/src/chainstate/v1.rs` -> `state`
- Entrypoint: a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only)
- Attacker controls: the full proposed block contents, its claimed tenure/burn view and reorg claim, and the gossiped signer messages
- Exploit idea: the message type is not bound into the signed hash
- Invariant to test: a signed response's meaning == the type the signer intended
- Expected Immunefi impact: Critical - rejection counted as accept
- Fast validation: test a replayed rejection
