# Q5224: assert_not_paused parser and runtime disagreement

## Question
Can an unprivileged attacker submit crafted bridge_inner, error to `assert_not_paused` so the parser, verifier, and runtime disagree on what was encoded, causing an invalid package or transaction to execute under assumptions that no longer hold?

## Target
- File/function: crates/sui-framework/packages/bridge/sources/bridge.move::assert_not_paused
- Entrypoint: Package publish or package upgrade transaction with crafted Move bytecode, metadata, or dependency state
- Attacker controls: bridge_inner, error
- Exploit idea: Probe alternative encodings, table lengths, index boundaries, and metadata forms that may normalize differently across stages.
- Invariant to test: Every accepted serialized input must have a single, stable meaning from decode through execution.
- Expected Immunefi impact: Critical or Medium — verifier bypass if exploitable for fund loss, otherwise harmful smart-contract behavior or node instability.
- Fast validation: Fuzz the encoding surface around this function’s consumed fields and compare decode, verify, and execute outcomes on a local network.
