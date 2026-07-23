# Q3245: assert_checkpoint_not_forked public-input invariant violation

## Question
Can an unprivileged attacker reach `assert_checkpoint_not_forked` through a normal transaction or remote request with crafted locally_built_checkpoint, verified_checkpoint, checkpoint_store and trigger a panic, fatal assertion, or invariant-violation error in unmodified validator or fullnode software?

## Target
- File/function: crates/sui-core/src/checkpoints/checkpoint_executor/utils.rs::assert_checkpoint_not_forked
- Entrypoint: Public transaction or remote fullnode/RPC request that reaches this path without privileged node control
- Attacker controls: locally_built_checkpoint, verified_checkpoint, checkpoint_store
- Exploit idea: Search for malformed but user-reachable inputs that pass earlier validation and only fail at a trusted internal assumption.
- Invariant to test: All public inputs must fail cleanly without panicking, aborting the process, or surfacing internal invariant-violation codes.
- Expected Immunefi impact: Low — transaction-triggered validator invariant violation or remote fullnode crash.
- Fast validation: Replay malformed local requests until the function is reached and record whether the process aborts instead of returning a normal error.
