# Q1789: debug logger prints session material in toSearchParams.ts

## Question
The logger emits privy:refresh lines and error objects at DEBUG; can an attacker cause toSearchParams (skips null/undefined to write token or code material into a log sink the app forwards off-device?

## Target
- File/function: [src/utils/toSearchParams.ts](src/utils/toSearchParams.ts) - toSearchParams (skips null/undefined, String() coercion)
- Entrypoint: PrivyInternal.getPath query building
- Attacker controls: query object values passed from public APIs
- Exploit idea: Enable DEBUG, run a refresh and a failed auth, and inspect the emitted lines.
- Invariant to test: No log line from src/utils/toSearchParams.ts may contain a token, verifier, or code value.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: capture logger output around toSearchParams (skips null/undefined and assert no stored credential substring appears.
