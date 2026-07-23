# Q15660: legacy_script_signature_checks parser and runtime disagreement

## Question
Can an unprivileged attacker submit crafted module, parameters_idx, return_idx to `legacy_script_signature_checks` so the parser, verifier, and runtime disagree on what was encoded, causing an invalid package or transaction to execute under assumptions that no longer hold?

## Target
- File/function: external-crates/move/crates/move-bytecode-verifier/src/script_signature.rs::legacy_script_signature_checks
- Entrypoint: Package publish or package upgrade transaction with crafted Move bytecode, metadata, or dependency state
- Attacker controls: module, parameters_idx, return_idx
- Exploit idea: Probe alternative encodings, table lengths, index boundaries, and metadata forms that may normalize differently across stages.
- Invariant to test: Every accepted serialized input must have a single, stable meaning from decode through execution.
- Expected Immunefi impact: Critical or Medium — verifier bypass if exploitable for fund loss, otherwise harmful smart-contract behavior or node instability.
- Fast validation: Fuzz the encoding surface around this function’s consumed fields and compare decode, verify, and execute outcomes on a local network.
