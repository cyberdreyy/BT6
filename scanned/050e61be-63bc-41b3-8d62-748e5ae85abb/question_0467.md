# Q467: build_committee_register_transaction cross-domain message binding gap

## Question
Can an unprivileged attacker drive `build_committee_register_transaction` with crafted validator_address, gas_object_ref, bridge_object_arg, bridge_authority_pub_key_bytes so that a message, proof, event, or signature remains valid after changing recipient, chain id, token identity, or message type, allowing unauthorized bridge execution or value redirection?

## Target
- File/function: crates/sui-bridge/src/sui_transaction_builder.rs::build_committee_register_transaction
- Entrypoint: Bridge deposit, claim, message-processing, or bridge-facing RPC flow reachable by an ordinary bridge user
- Attacker controls: validator_address, gas_object_ref, bridge_object_arg, bridge_authority_pub_key_bytes
- Exploit idea: Look for incomplete domain separation between the signed or proven payload and the state transition that consumes it.
- Invariant to test: Every accepted bridge message must be uniquely bound to its source chain, destination chain, token identity, amount, recipient, and action type.
- Expected Immunefi impact: Critical — bridge message forgery or misbinding enabling theft, redirection, or illegitimate minting of bridged assets.
- Fast validation: Start from a valid local bridge flow, mutate one bound field at a time, and test whether execution still succeeds or redirects value.
