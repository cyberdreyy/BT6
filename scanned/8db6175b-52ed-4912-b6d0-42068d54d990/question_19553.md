# Q19553: linkage_table verifier acceptance of forbidden package state

## Question
Can an unprivileged attacker submit a package publish or upgrade that reaches `linkage_table` in `sui-execution/latest/sui-adapter/src/static_programmable_transactions/linkage/config.rs` with crafted pkg, make invalid bytecode or metadata pass validation, and then use the accepted package to create, copy, transfer, or load objects in a way the verifier should forbid?

## Target
- File/function: sui-execution/latest/sui-adapter/src/static_programmable_transactions/linkage/config.rs::linkage_table
- Entrypoint: Package publish or package upgrade transaction with crafted Move bytecode, metadata, or dependency state
- Attacker controls: pkg
- Exploit idea: Look for mismatches between parsing, verification, and runtime assumptions about abilities, signers, ownership, or module linkage.
- Invariant to test: No user-supplied package may be accepted unless verifier and runtime agree that every authority, ownership, and type-safety rule holds.
- Expected Immunefi impact: Critical — verifier bypass chained to unauthorized object creation, transfer, dynamic loading, or fund theft.
- Fast validation: Mutate a local package around the fields consumed here, publish it on a private network, and attempt an unauthorized object or balance transition.
