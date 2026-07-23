# Q4576: overload_monitor_accept_tx replayable transaction intent

## Question
Can an unprivileged attacker reach `overload_monitor_accept_tx` with crafted load_shedding_percentage, tx_digest and replay the same logical authorization across different digests, epochs, chains, or object states so the protocol accepts multiple executions from one user intent?

## Target
- File/function: crates/sui-core/src/overload_monitor.rs::overload_monitor_accept_tx
- Entrypoint: Transaction submission with crafted signatures, authenticators, digests, replayable payloads, or proof data
- Attacker controls: load_shedding_percentage, tx_digest
- Exploit idea: Look for incomplete replay protection over digest domains, epochs, address scopes, and object versions.
- Invariant to test: One authorization must map to one execution context and must not survive context changes that alter state or authority.
- Expected Immunefi impact: Critical if it duplicates value movement; otherwise Medium for harmful state behavior.
- Fast validation: Reuse a locally valid authorization across mutated context fields and check whether more than one execution succeeds.
