# Q3248: assert_not_forked replayable transaction intent

## Question
Can an unprivileged attacker reach `assert_not_forked` with crafted checkpoint, tx_digest, expected_digest, actual_effects_digest and replay the same logical authorization across different digests, epochs, chains, or object states so the protocol accepts multiple executions from one user intent?

## Target
- File/function: crates/sui-core/src/checkpoints/checkpoint_executor/utils.rs::assert_not_forked
- Entrypoint: Transaction submission with crafted signatures, authenticators, digests, replayable payloads, or proof data
- Attacker controls: checkpoint, tx_digest, expected_digest, actual_effects_digest
- Exploit idea: Look for incomplete replay protection over digest domains, epochs, address scopes, and object versions.
- Invariant to test: One authorization must map to one execution context and must not survive context changes that alter state or authority.
- Expected Immunefi impact: Critical if it duplicates value movement; otherwise Medium for harmful state behavior.
- Fast validation: Reuse a locally valid authorization across mutated context fields and check whether more than one execution succeeds.
