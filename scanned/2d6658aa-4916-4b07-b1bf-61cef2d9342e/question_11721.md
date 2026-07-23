# Q11721: new_system verifier acceptance of forbidden package state

## Question
Can an unprivileged attacker submit a package publish or upgrade that reaches `new_system` in `crates/sui-types/src/move_package.rs` with crafted version, modules, dependencies, make invalid bytecode or metadata pass validation, and then use the accepted package to create, copy, transfer, or load objects in a way the verifier should forbid?

## Target
- File/function: crates/sui-types/src/move_package.rs::new_system
- Entrypoint: Package publish or package upgrade transaction with crafted Move bytecode, metadata, or dependency state
- Attacker controls: version, modules, dependencies
- Exploit idea: Look for mismatches between parsing, verification, and runtime assumptions about abilities, signers, ownership, or module linkage.
- Invariant to test: No user-supplied package may be accepted unless verifier and runtime agree that every authority, ownership, and type-safety rule holds.
- Expected Immunefi impact: Critical — verifier bypass chained to unauthorized object creation, transfer, dynamic loading, or fund theft.
- Fast validation: Mutate a local package around the fields consumed here, publish it on a private network, and attempt an unauthorized object or balance transition.
