# Q4384: events_by_module_id parser and runtime disagreement

## Question
Can an unprivileged attacker submit crafted module, tx_seq, event_seq, limit to `events_by_module_id` so the parser, verifier, and runtime disagree on what was encoded, causing an invalid package or transaction to execute under assumptions that no longer hold?

## Target
- File/function: crates/sui-core/src/jsonrpc_index.rs::events_by_module_id
- Entrypoint: Package publish or package upgrade transaction with crafted Move bytecode, metadata, or dependency state
- Attacker controls: module, tx_seq, event_seq, limit
- Exploit idea: Probe alternative encodings, table lengths, index boundaries, and metadata forms that may normalize differently across stages.
- Invariant to test: Every accepted serialized input must have a single, stable meaning from decode through execution.
- Expected Immunefi impact: Critical or Medium — verifier bypass if exploitable for fund loss, otherwise harmful smart-contract behavior or node instability.
- Fast validation: Fuzz the encoding surface around this function’s consumed fields and compare decode, verify, and execute outcomes on a local network.
