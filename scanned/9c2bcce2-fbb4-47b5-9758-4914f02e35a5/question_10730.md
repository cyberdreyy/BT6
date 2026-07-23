# Q10730: native_function_threshold_exceeded replayable transaction intent

## Question
Can an unprivileged attacker reach `native_function_threshold_exceeded` with crafted gas_model_version, num_native_calls and replay the same logical authorization across different digests, epochs, chains, or object states so the protocol accepts multiple executions from one user intent?

## Target
- File/function: crates/sui-types/src/gas_model/gas_predicates.rs::native_function_threshold_exceeded
- Entrypoint: Transaction submission with crafted signatures, authenticators, digests, replayable payloads, or proof data
- Attacker controls: gas_model_version, num_native_calls
- Exploit idea: Look for incomplete replay protection over digest domains, epochs, address scopes, and object versions.
- Invariant to test: One authorization must map to one execution context and must not survive context changes that alter state or authority.
- Expected Immunefi impact: Critical if it duplicates value movement; otherwise Medium for harmful state behavior.
- Fast validation: Reuse a locally valid authorization across mutated context fields and check whether more than one execution succeeds.
