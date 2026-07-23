# Q1764: new_at_next_epoch parser and runtime disagreement

## Question
Can an unprivileged attacker submit crafted name, new_committee, epoch_start_configuration, backing_package_store to `new_at_next_epoch` so the parser, verifier, and runtime disagree on what was encoded, causing an invalid package or transaction to execute under assumptions that no longer hold?

## Target
- File/function: crates/sui-core/src/authority/authority_per_epoch_store.rs::new_at_next_epoch
- Entrypoint: Package publish or package upgrade transaction with crafted Move bytecode, metadata, or dependency state
- Attacker controls: name, new_committee, epoch_start_configuration, backing_package_store
- Exploit idea: Probe alternative encodings, table lengths, index boundaries, and metadata forms that may normalize differently across stages.
- Invariant to test: Every accepted serialized input must have a single, stable meaning from decode through execution.
- Expected Immunefi impact: Critical or Medium — verifier bypass if exploitable for fund loss, otherwise harmful smart-contract behavior or node instability.
- Fast validation: Fuzz the encoding surface around this function’s consumed fields and compare decode, verify, and execute outcomes on a local network.
