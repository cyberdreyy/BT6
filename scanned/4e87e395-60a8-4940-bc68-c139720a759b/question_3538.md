# Q3538: record_transaction_fork_detected replayable transaction intent

## Question
Can an unprivileged attacker reach `record_transaction_fork_detected` with crafted tx_digest, expected_effects_digest, actual_effects_digest, certified_checkpoint_seq and replay the same logical authorization across different digests, epochs, chains, or object states so the protocol accepts multiple executions from one user intent?

## Target
- File/function: crates/sui-core/src/checkpoints/mod.rs::record_transaction_fork_detected
- Entrypoint: Transaction submission with crafted signatures, authenticators, digests, replayable payloads, or proof data
- Attacker controls: tx_digest, expected_effects_digest, actual_effects_digest, certified_checkpoint_seq
- Exploit idea: Look for incomplete replay protection over digest domains, epochs, address scopes, and object versions.
- Invariant to test: One authorization must map to one execution context and must not survive context changes that alter state or authority.
- Expected Immunefi impact: Critical if it duplicates value movement; otherwise Medium for harmful state behavior.
- Fast validation: Reuse a locally valid authorization across mutated context fields and check whether more than one execution succeeds.
