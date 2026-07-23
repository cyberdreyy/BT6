# Q4533: new_without_init public-input invariant violation

## Question
Can an unprivileged attacker reach `new_without_init` through a normal transaction or remote request with crafted path, registry, max_type_length, remove_deprecated_tables and trigger a panic, fatal assertion, or invariant-violation error in unmodified validator or fullnode software?

## Target
- File/function: crates/sui-core/src/jsonrpc_index.rs::new_without_init
- Entrypoint: Public transaction or remote fullnode/RPC request that reaches this path without privileged node control
- Attacker controls: path, registry, max_type_length, remove_deprecated_tables
- Exploit idea: Search for malformed but user-reachable inputs that pass earlier validation and only fail at a trusted internal assumption.
- Invariant to test: All public inputs must fail cleanly without panicking, aborting the process, or surfacing internal invariant-violation codes.
- Expected Immunefi impact: Low — transaction-triggered validator invariant violation or remote fullnode crash.
- Fast validation: Replay malformed local requests until the function is reached and record whether the process aborts instead of returning a normal error.
