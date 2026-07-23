# Q337: get_bridge_action_by_tx_digest_and_event_idx_maybe cross-domain message binding gap

## Question
Can an unprivileged attacker drive `get_bridge_action_by_tx_digest_and_event_idx_maybe` with crafted tx_digest, event_idx so that a message, proof, event, or signature remains valid after changing recipient, chain id, token identity, or message type, allowing unauthorized bridge execution or value redirection?

## Target
- File/function: crates/sui-bridge/src/sui_client.rs::get_bridge_action_by_tx_digest_and_event_idx_maybe
- Entrypoint: Bridge deposit, claim, message-processing, or bridge-facing RPC flow reachable by an ordinary bridge user
- Attacker controls: tx_digest, event_idx
- Exploit idea: Look for incomplete domain separation between the signed or proven payload and the state transition that consumes it.
- Invariant to test: Every accepted bridge message must be uniquely bound to its source chain, destination chain, token identity, amount, recipient, and action type.
- Expected Immunefi impact: Critical — bridge message forgery or misbinding enabling theft, redirection, or illegitimate minting of bridged assets.
- Fast validation: Start from a valid local bridge flow, mutate one bound field at a time, and test whether execution still succeeds or redirects value.
