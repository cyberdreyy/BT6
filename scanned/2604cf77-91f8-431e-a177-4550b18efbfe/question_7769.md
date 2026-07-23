# Q7769: per_type_exists parser and runtime disagreement

## Question
Can an unprivileged attacker submit crafted deny_list, per_type_index, per_type_key to `per_type_exists` so the parser, verifier, and runtime disagree on what was encoded, causing an invalid package or transaction to execute under assumptions that no longer hold?

## Target
- File/function: crates/sui-framework/packages/sui-framework/sources/deny_list.move::per_type_exists
- Entrypoint: Package publish or package upgrade transaction with crafted Move bytecode, metadata, or dependency state
- Attacker controls: deny_list, per_type_index, per_type_key
- Exploit idea: Probe alternative encodings, table lengths, index boundaries, and metadata forms that may normalize differently across stages.
- Invariant to test: Every accepted serialized input must have a single, stable meaning from decode through execution.
- Expected Immunefi impact: Critical or Medium — verifier bypass if exploitable for fund loss, otherwise harmful smart-contract behavior or node instability.
- Fast validation: Fuzz the encoding surface around this function’s consumed fields and compare decode, verify, and execute outcomes on a local network.
