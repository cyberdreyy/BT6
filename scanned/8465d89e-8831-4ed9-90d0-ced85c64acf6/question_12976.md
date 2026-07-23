# Q12976: into_sui_validator_summary cross-domain message binding gap

## Question
Can an unprivileged attacker drive `into_sui_validator_summary` with crafted signature bytes, authenticator payloads, digests, object references, and type data so that a message, proof, event, or signature remains valid after changing recipient, chain id, token identity, or message type, allowing unauthorized bridge execution or value redirection?

## Target
- File/function: crates/sui-types/src/sui_system_state/sui_system_state_inner_v1.rs::into_sui_validator_summary
- Entrypoint: Bridge deposit, claim, message-processing, or bridge-facing RPC flow reachable by an ordinary bridge user
- Attacker controls: signature bytes, authenticator payloads, digests, object references, and type data
- Exploit idea: Look for incomplete domain separation between the signed or proven payload and the state transition that consumes it.
- Invariant to test: Every accepted bridge message must be uniquely bound to its source chain, destination chain, token identity, amount, recipient, and action type.
- Expected Immunefi impact: Critical — bridge message forgery or misbinding enabling theft, redirection, or illegitimate minting of bridged assets.
- Fast validation: Start from a valid local bridge flow, mutate one bound field at a time, and test whether execution still succeeds or redirects value.
