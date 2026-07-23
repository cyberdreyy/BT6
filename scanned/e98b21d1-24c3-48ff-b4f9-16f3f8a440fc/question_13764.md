# Q13764: new_with_gas_data_and_expiration replayable transaction intent

## Question
Can an unprivileged attacker reach `new_with_gas_data_and_expiration` with crafted kind, sender, gas_data, expiration and replay the same logical authorization across different digests, epochs, chains, or object states so the protocol accepts multiple executions from one user intent?

## Target
- File/function: crates/sui-types/src/transaction.rs::new_with_gas_data_and_expiration
- Entrypoint: Transaction submission with crafted signatures, authenticators, digests, replayable payloads, or proof data
- Attacker controls: kind, sender, gas_data, expiration
- Exploit idea: Look for incomplete replay protection over digest domains, epochs, address scopes, and object versions.
- Invariant to test: One authorization must map to one execution context and must not survive context changes that alter state or authority.
- Expected Immunefi impact: Critical if it duplicates value movement; otherwise Medium for harmful state behavior.
- Fast validation: Reuse a locally valid authorization across mutated context fields and check whether more than one execution succeeds.
