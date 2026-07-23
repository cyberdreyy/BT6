# Q15978: tag public-input invariant violation

## Question
Can an unprivileged attacker reach `tag` through a normal transaction or remote request with crafted serialized inputs, object references, and transaction parameters and trigger a panic, fatal assertion, or invariant-violation error in unmodified validator or fullnode software?

## Target
- File/function: external-crates/move/crates/move-core-types/src/annotated_visitor.rs::tag
- Entrypoint: Public transaction or remote fullnode/RPC request that reaches this path without privileged node control
- Attacker controls: serialized inputs, object references, and transaction parameters
- Exploit idea: Search for malformed but user-reachable inputs that pass earlier validation and only fail at a trusted internal assumption.
- Invariant to test: All public inputs must fail cleanly without panicking, aborting the process, or surfacing internal invariant-violation codes.
- Expected Immunefi impact: Low — transaction-triggered validator invariant violation or remote fullnode crash.
- Fast validation: Replay malformed local requests until the function is reached and record whether the process aborts instead of returning a normal error.
