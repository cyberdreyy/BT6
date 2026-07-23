# Q5725: add_tokens_on_sui cross-domain message binding gap

## Question
Can an unprivileged attacker drive `add_tokens_on_sui` with crafted message bytes, proof fields, amount, recipient, nonce, and chain-domain values so that a message, proof, event, or signature remains valid after changing recipient, chain id, token identity, or message type, allowing unauthorized bridge execution or value redirection?

## Target
- File/function: crates/sui-framework/packages/bridge/sources/message_types.move::add_tokens_on_sui
- Entrypoint: Bridge deposit, claim, message-processing, or bridge-facing RPC flow reachable by an ordinary bridge user
- Attacker controls: message bytes, proof fields, amount, recipient, nonce, and chain-domain values
- Exploit idea: Look for incomplete domain separation between the signed or proven payload and the state transition that consumes it.
- Invariant to test: Every accepted bridge message must be uniquely bound to its source chain, destination chain, token identity, amount, recipient, and action type.
- Expected Immunefi impact: Critical — bridge message forgery or misbinding enabling theft, redirection, or illegitimate minting of bridged assets.
- Fast validation: Start from a valid local bridge flow, mutate one bound field at a time, and test whether execution still succeeds or redirects value.
