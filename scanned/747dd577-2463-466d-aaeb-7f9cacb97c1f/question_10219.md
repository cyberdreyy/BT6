# Q10219: epoch_start_timestamp replayable transaction intent

## Question
Can an unprivileged attacker reach `epoch_start_timestamp` with crafted signature bytes, authenticator payloads, digests, object references, and type data and replay the same logical authorization across different digests, epochs, chains, or object states so the protocol accepts multiple executions from one user intent?

## Target
- File/function: crates/sui-types/src/epoch_data.rs::epoch_start_timestamp
- Entrypoint: Transaction submission with crafted signatures, authenticators, digests, replayable payloads, or proof data
- Attacker controls: signature bytes, authenticator payloads, digests, object references, and type data
- Exploit idea: Look for incomplete replay protection over digest domains, epochs, address scopes, and object versions.
- Invariant to test: One authorization must map to one execution context and must not survive context changes that alter state or authority.
- Expected Immunefi impact: Critical if it duplicates value movement; otherwise Medium for harmful state behavior.
- Fast validation: Reuse a locally valid authorization across mutated context fields and check whether more than one execution succeeds.
