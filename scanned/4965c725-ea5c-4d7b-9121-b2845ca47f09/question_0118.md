# Q118: get_events_in_range public-input invariant violation

## Question
Can an unprivileged attacker reach `get_events_in_range` through a normal transaction or remote request with crafted address, start_block, end_block and trigger a panic, fatal assertion, or invariant-violation error in unmodified validator or fullnode software?

## Target
- File/function: crates/sui-bridge/src/eth_client.rs::get_events_in_range
- Entrypoint: Public transaction or remote fullnode/RPC request that reaches this path without privileged node control
- Attacker controls: address, start_block, end_block
- Exploit idea: Search for malformed but user-reachable inputs that pass earlier validation and only fail at a trusted internal assumption.
- Invariant to test: All public inputs must fail cleanly without panicking, aborting the process, or surfacing internal invariant-violation codes.
- Expected Immunefi impact: Low — transaction-triggered validator invariant violation or remote fullnode crash.
- Fast validation: Replay malformed local requests until the function is reached and record whether the process aborts instead of returning a normal error.
