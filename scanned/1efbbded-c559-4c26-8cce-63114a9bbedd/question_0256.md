# Q256: run_with_grpc public-input invariant violation

## Question
Can an unprivileged attacker reach `run_with_grpc` through a normal transaction or remote request with crafted bridge_action_executor and trigger a panic, fatal assertion, or invariant-violation error in unmodified validator or fullnode software?

## Target
- File/function: crates/sui-bridge/src/orchestrator.rs::run_with_grpc
- Entrypoint: Public transaction or remote fullnode/RPC request that reaches this path without privileged node control
- Attacker controls: bridge_action_executor
- Exploit idea: Search for malformed but user-reachable inputs that pass earlier validation and only fail at a trusted internal assumption.
- Invariant to test: All public inputs must fail cleanly without panicking, aborting the process, or surfacing internal invariant-violation codes.
- Expected Immunefi impact: Low — transaction-triggered validator invariant violation or remote fullnode crash.
- Fast validation: Replay malformed local requests until the function is reached and record whether the process aborts instead of returning a normal error.
