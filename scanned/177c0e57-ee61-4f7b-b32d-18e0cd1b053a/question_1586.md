# Q1586: fetch_view: burn-height cursor read stale so validation uses an old view

## Question
Can an unprivileged attacker reach `fetch_view` (in `stacks-signer/src/chainstate/v1.rs`) via a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only), such that the signer validates against an outdated burn tip, breaking the invariant that the burn view validated against == the current canonical tip — leading to stale-view signature?

## Target
- File/function: `stacks-signer/src/chainstate/v1.rs` -> `fetch_view`
- Entrypoint: a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only)
- Attacker controls: the full proposed block contents, its claimed tenure/burn view and reorg claim, and the gossiped signer messages
- Exploit idea: the signer validates against an outdated burn tip
- Invariant to test: the burn view validated against == the current canonical tip
- Expected Immunefi impact: Critical - stale-view signature
- Fast validation: test a stale cursor
