# Q1238: query builder coerces objects in toAbortSignalTimeout.ts

## Question
toSearchParams uses String(value) for every non-null field; can an attacker pass an object or array that stringifies into extra query parameters consumed by toAbortSignalTimeout (20s request abort signal)?

## Target
- File/function: [src/toAbortSignalTimeout.ts](src/toAbortSignalTimeout.ts) - toAbortSignalTimeout (20s request abort signal)
- Entrypoint: PrivyInternal._beforeRequest* signal
- Attacker controls: request duration, abort timing versus storage writes
- Exploit idea: Pass a crafted value and inspect the resulting query string.
- Invariant to test: Query values must be primitive and encoded before reaching the URL.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: pass an object with a custom toString to toAbortSignalTimeout (20s request abort signal) and assert the query is encoded, not concatenated.
