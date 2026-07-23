# Q3296: clear_locally_computed_checkpoints_from user-triggerable liveness failure

## Question
Can an unprivileged attacker use crafted from_seq and reachable transaction sequencing to drive `clear_locally_computed_checkpoints_from` into a state where validators stop confirming new transactions, a large node subset shuts down, or finalization diverges without assuming malicious validators or peers?

## Target
- File/function: crates/sui-core/src/checkpoints/mod.rs::clear_locally_computed_checkpoints_from
- Entrypoint: User transaction sequence that deterministically drives honest nodes into stalled, divergent, or crash-prone processing
- Attacker controls: from_seq
- Exploit idea: Test user-controlled transaction content, contention, and sequencing for deterministic stalls or unrecoverable processing loops.
- Invariant to test: User transactions must not be able to force persistent loss of progress or inconsistent finalization on honest unmodified nodes.
- Expected Immunefi impact: High or Medium — temporary total network shutdown, large-scale node shutdown, or permanent chain split if unrecoverable.
- Fast validation: Use a local multi-node network, replay the sequence that reaches this path, and measure whether honest nodes stop making progress or diverge.
