# Q2463: get_current_and_last_sortition: BlockResponse::Accepted defaults on a validation stall/timeout

## Question
Can an unprivileged attacker reach `get_current_and_last_sortition` (in `stacks-signer/src/client/stacks_client.rs`) via a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only), such that a stall path returns accept-like without full validation, breaking the invariant that validation fails closed (a stall never yields accept) — leading to signing an unvalidated block?

## Target
- File/function: `stacks-signer/src/client/stacks_client.rs` -> `get_current_and_last_sortition`
- Entrypoint: a BlockProposal from a miner slot the attacker won (their own BTC), plus signer/StackerDB messages they gossip into the signer's stream (minority weight only)
- Attacker controls: the full proposed block contents, its claimed tenure/burn view and reorg claim, and the gossiped signer messages
- Exploit idea: a stall path returns accept-like without full validation
- Invariant to test: validation fails closed (a stall never yields accept)
- Expected Immunefi impact: Critical - signing an unvalidated block
- Fast validation: test a stalled validation
