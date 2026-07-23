# Q64: rpc_urls public-input invariant violation

## Question
Can an unprivileged attacker reach `rpc_urls` through a normal transaction or remote request with crafted message bytes, proof fields, amount, recipient, nonce, and chain-domain values and trigger a panic, fatal assertion, or invariant-violation error in unmodified validator or fullnode software?

## Target
- File/function: crates/sui-bridge/src/config.rs::rpc_urls
- Entrypoint: Public transaction or remote fullnode/RPC request that reaches this path without privileged node control
- Attacker controls: message bytes, proof fields, amount, recipient, nonce, and chain-domain values
- Exploit idea: Search for malformed but user-reachable inputs that pass earlier validation and only fail at a trusted internal assumption.
- Invariant to test: All public inputs must fail cleanly without panicking, aborting the process, or surfacing internal invariant-violation codes.
- Expected Immunefi impact: Low — transaction-triggered validator invariant violation or remote fullnode crash.
- Fast validation: Replay malformed local requests until the function is reached and record whether the process aborts instead of returning a normal error.
