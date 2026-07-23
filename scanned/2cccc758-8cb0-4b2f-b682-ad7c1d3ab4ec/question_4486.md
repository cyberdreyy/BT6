# Q4486: get_transactions_by_move_function verifier acceptance of forbidden package state

## Question
Can an unprivileged attacker submit a package publish or upgrade that reaches `get_transactions_by_move_function` in `crates/sui-core/src/jsonrpc_index.rs` with crafted package, module, function, cursor, make invalid bytecode or metadata pass validation, and then use the accepted package to create, copy, transfer, or load objects in a way the verifier should forbid?

## Target
- File/function: crates/sui-core/src/jsonrpc_index.rs::get_transactions_by_move_function
- Entrypoint: Package publish or package upgrade transaction with crafted Move bytecode, metadata, or dependency state
- Attacker controls: package, module, function, cursor
- Exploit idea: Look for mismatches between parsing, verification, and runtime assumptions about abilities, signers, ownership, or module linkage.
- Invariant to test: No user-supplied package may be accepted unless verifier and runtime agree that every authority, ownership, and type-safety rule holds.
- Expected Immunefi impact: Critical — verifier bypass chained to unauthorized object creation, transfer, dynamic loading, or fund theft.
- Fast validation: Mutate a local package around the fields consumed here, publish it on a private network, and attempt an unauthorized object or balance transition.
