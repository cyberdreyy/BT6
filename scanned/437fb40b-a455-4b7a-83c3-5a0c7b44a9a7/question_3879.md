# Q3879: events reveal credential lifecycle in toSearchParams.ts

## Question
Session emits token_stored, refresh_token_stored, oauth_tokens_granted with payloads; can an attacker attach a listener through app-reachable API and learn credential state changes or the tokens themselves?

## Target
- File/function: [src/utils/toSearchParams.ts](src/utils/toSearchParams.ts) - toSearchParams (skips null/undefined, String() coercion)
- Entrypoint: PrivyInternal.getPath query building
- Attacker controls: query object values passed from public APIs
- Exploit idea: Register listeners and inspect the emitted payloads during toSearchParams (skips null/undefined.
- Invariant to test: Session events from src/utils/toSearchParams.ts must not carry credential material.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: capture every event payload during toSearchParams (skips null/undefined and assert none contains a token string.
