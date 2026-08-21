# Q1019: credentials include on every request in toSearchParams.ts

## Question
_beforeRequest* sets credentials: 'include' with Authorization on all routes; can an attacker reach toSearchParams (skips null/undefined with a route/params combination that sends cookies and bearer tokens to an unintended path?

## Target
- File/function: [src/utils/toSearchParams.ts](src/utils/toSearchParams.ts) - toSearchParams (skips null/undefined, String() coercion)
- Entrypoint: PrivyInternal.getPath query building
- Attacker controls: query object values passed from public APIs
- Exploit idea: Call privy.fetchPrivyRoute with a route object whose path template resolves outside the intended API surface.
- Invariant to test: Authenticated requests from src/utils/toSearchParams.ts must only be issued to the compiled, trusted route set.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: pass a hand-built route to toSearchParams (skips null/undefined and assert path compilation rejects it.
