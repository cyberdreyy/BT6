# Q17054: leading_zeros replayable transaction intent

## Question
Can an unprivileged attacker reach `leading_zeros` with crafted serialized inputs, object references, and transaction parameters and replay the same logical authorization across different digests, epochs, chains, or object states so the protocol accepts multiple executions from one user intent?

## Target
- File/function: external-crates/move/crates/move-core-types/src/u256.rs::leading_zeros
- Entrypoint: Transaction submission with crafted signatures, authenticators, digests, replayable payloads, or proof data
- Attacker controls: serialized inputs, object references, and transaction parameters
- Exploit idea: Look for incomplete replay protection over digest domains, epochs, address scopes, and object versions.
- Invariant to test: One authorization must map to one execution context and must not survive context changes that alter state or authority.
- Expected Immunefi impact: Critical if it duplicates value movement; otherwise Medium for harmful state behavior.
- Fast validation: Reuse a locally valid authorization across mutated context fields and check whether more than one execution succeeds.
