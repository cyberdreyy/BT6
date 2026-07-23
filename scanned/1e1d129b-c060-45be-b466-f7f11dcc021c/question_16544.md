# Q16544: is_std_string replayable transaction intent

## Question
Can an unprivileged attacker reach `is_std_string` with crafted move_std_addr and replay the same logical authorization across different digests, epochs, chains, or object states so the protocol accepts multiple executions from one user intent?

## Target
- File/function: external-crates/move/crates/move-core-types/src/language_storage.rs::is_std_string
- Entrypoint: Transaction submission with crafted signatures, authenticators, digests, replayable payloads, or proof data
- Attacker controls: move_std_addr
- Exploit idea: Look for incomplete replay protection over digest domains, epochs, address scopes, and object versions.
- Invariant to test: One authorization must map to one execution context and must not survive context changes that alter state or authority.
- Expected Immunefi impact: Critical if it duplicates value movement; otherwise Medium for harmful state behavior.
- Fast validation: Reuse a locally valid authorization across mutated context fields and check whether more than one execution succeeds.
