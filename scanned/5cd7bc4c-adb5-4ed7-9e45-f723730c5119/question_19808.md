# Q19808: into_effects replayable transaction intent

## Question
Can an unprivileged attacker reach `into_effects` with crafted self, shared_object_refs, transaction_digest, transaction_dependencies and replay the same logical authorization across different digests, epochs, chains, or object states so the protocol accepts multiple executions from one user intent?

## Target
- File/function: sui-execution/latest/sui-adapter/src/temporary_store.rs::into_effects
- Entrypoint: Transaction submission with crafted signatures, authenticators, digests, replayable payloads, or proof data
- Attacker controls: self, shared_object_refs, transaction_digest, transaction_dependencies
- Exploit idea: Look for incomplete replay protection over digest domains, epochs, address scopes, and object versions.
- Invariant to test: One authorization must map to one execution context and must not survive context changes that alter state or authority.
- Expected Immunefi impact: Critical if it duplicates value movement; otherwise Medium for harmful state behavior.
- Fast validation: Reuse a locally valid authorization across mutated context fields and check whether more than one execution succeeds.
