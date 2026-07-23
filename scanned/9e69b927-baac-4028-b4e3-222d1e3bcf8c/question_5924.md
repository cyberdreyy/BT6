# Q5924: create_from_raw_value parser and runtime disagreement

## Question
Can an unprivileged attacker submit crafted value to `create_from_raw_value` so the parser, verifier, and runtime disagree on what was encoded, causing an invalid package or transaction to execute under assumptions that no longer hold?

## Target
- File/function: crates/sui-framework/packages/move-stdlib/sources/fixed_point32.move::create_from_raw_value
- Entrypoint: Package publish or package upgrade transaction with crafted Move bytecode, metadata, or dependency state
- Attacker controls: value
- Exploit idea: Probe alternative encodings, table lengths, index boundaries, and metadata forms that may normalize differently across stages.
- Invariant to test: Every accepted serialized input must have a single, stable meaning from decode through execution.
- Expected Immunefi impact: Critical or Medium — verifier bypass if exploitable for fund loss, otherwise harmful smart-contract behavior or node instability.
- Fast validation: Fuzz the encoding surface around this function’s consumed fields and compare decode, verify, and execute outcomes on a local network.
