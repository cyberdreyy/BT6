# Q19341: take_user_events cross-domain message binding gap

## Question
Can an unprivileged attacker drive `take_user_events` with crafted vm, version_mid, function_def_idx, instr_length so that a message, proof, event, or signature remains valid after changing recipient, chain id, token identity, or message type, allowing unauthorized bridge execution or value redirection?

## Target
- File/function: sui-execution/latest/sui-adapter/src/static_programmable_transactions/execution/context.rs::take_user_events
- Entrypoint: Bridge deposit, claim, message-processing, or bridge-facing RPC flow reachable by an ordinary bridge user
- Attacker controls: vm, version_mid, function_def_idx, instr_length
- Exploit idea: Look for incomplete domain separation between the signed or proven payload and the state transition that consumes it.
- Invariant to test: Every accepted bridge message must be uniquely bound to its source chain, destination chain, token identity, amount, recipient, and action type.
- Expected Immunefi impact: Critical — bridge message forgery or misbinding enabling theft, redirection, or illegitimate minting of bridged assets.
- Fast validation: Start from a valid local bridge flow, mutate one bound field at a time, and test whether execution still succeeds or redirects value.
