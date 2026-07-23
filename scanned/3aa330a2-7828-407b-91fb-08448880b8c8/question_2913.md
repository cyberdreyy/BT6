# Q2913: should_defer_due_to_object_congestion replayable transaction intent

## Question
Can an unprivileged attacker reach `should_defer_due_to_object_congestion` with crafted cert, previously_deferred_tx_digests, commit_info and replay the same logical authorization across different digests, epochs, chains, or object states so the protocol accepts multiple executions from one user intent?

## Target
- File/function: crates/sui-core/src/authority/shared_object_congestion_tracker.rs::should_defer_due_to_object_congestion
- Entrypoint: Transaction submission with crafted signatures, authenticators, digests, replayable payloads, or proof data
- Attacker controls: cert, previously_deferred_tx_digests, commit_info
- Exploit idea: Look for incomplete replay protection over digest domains, epochs, address scopes, and object versions.
- Invariant to test: One authorization must map to one execution context and must not survive context changes that alter state or authority.
- Expected Immunefi impact: Critical if it duplicates value movement; otherwise Medium for harmful state behavior.
- Fast validation: Reuse a locally valid authorization across mutated context fields and check whether more than one execution succeeds.
