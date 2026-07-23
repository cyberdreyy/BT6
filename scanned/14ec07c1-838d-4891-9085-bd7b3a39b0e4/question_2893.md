# Q2893: get_tx_cost public-input invariant violation

## Question
Can an unprivileged attacker reach `get_tx_cost` through a normal transaction or remote request with crafted execution_time_estimator, cert, indirect_state_observer and trigger a panic, fatal assertion, or invariant-violation error in unmodified validator or fullnode software?

## Target
- File/function: crates/sui-core/src/authority/shared_object_congestion_tracker.rs::get_tx_cost
- Entrypoint: Public transaction or remote fullnode/RPC request that reaches this path without privileged node control
- Attacker controls: execution_time_estimator, cert, indirect_state_observer
- Exploit idea: Search for malformed but user-reachable inputs that pass earlier validation and only fail at a trusted internal assumption.
- Invariant to test: All public inputs must fail cleanly without panicking, aborting the process, or surfacing internal invariant-violation codes.
- Expected Immunefi impact: Low — transaction-triggered validator invariant violation or remote fullnode crash.
- Fast validation: Replay malformed local requests until the function is reached and record whether the process aborts instead of returning a normal error.
