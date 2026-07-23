# Q461: build_add_tokens_on_sui_transaction cross-domain message binding gap

## Question
Can an unprivileged attacker drive `build_add_tokens_on_sui_transaction` with crafted client_address, gas_object_ref, action, bridge_object_arg so that a message, proof, event, or signature remains valid after changing recipient, chain id, token identity, or message type, allowing unauthorized bridge execution or value redirection?

## Target
- File/function: crates/sui-bridge/src/sui_transaction_builder.rs::build_add_tokens_on_sui_transaction
- Entrypoint: Bridge deposit, claim, message-processing, or bridge-facing RPC flow reachable by an ordinary bridge user
- Attacker controls: client_address, gas_object_ref, action, bridge_object_arg
- Exploit idea: Look for incomplete domain separation between the signed or proven payload and the state transition that consumes it.
- Invariant to test: Every accepted bridge message must be uniquely bound to its source chain, destination chain, token identity, amount, recipient, and action type.
- Expected Immunefi impact: Critical — bridge message forgery or misbinding enabling theft, redirection, or illegitimate minting of bridged assets.
- Fast validation: Start from a valid local bridge flow, mutate one bound field at a time, and test whether execution still succeeds or redirects value.
