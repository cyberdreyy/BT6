# Q3016: record_submitted_tx public-input invariant violation

## Question
Can an unprivileged attacker reach `record_submitted_tx` through a normal transaction or remote request with crafted digest, amplification_factor, submitter_client_addr and trigger a panic, fatal assertion, or invariant-violation error in unmodified validator or fullnode software?

## Target
- File/function: crates/sui-core/src/authority/submitted_transaction_cache.rs::record_submitted_tx
- Entrypoint: Public transaction or remote fullnode/RPC request that reaches this path without privileged node control
- Attacker controls: digest, amplification_factor, submitter_client_addr
- Exploit idea: Search for malformed but user-reachable inputs that pass earlier validation and only fail at a trusted internal assumption.
- Invariant to test: All public inputs must fail cleanly without panicking, aborting the process, or surfacing internal invariant-violation codes.
- Expected Immunefi impact: Low — transaction-triggered validator invariant violation or remote fullnode crash.
- Fast validation: Replay malformed local requests until the function is reached and record whether the process aborts instead of returning a normal error.
