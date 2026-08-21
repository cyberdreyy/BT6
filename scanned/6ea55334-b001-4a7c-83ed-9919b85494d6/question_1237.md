# Q1237: query builder coerces objects in Error.ts

## Question
toSearchParams uses String(value) for every non-null field; can an attacker pass an object or array that stringifies into extra query parameters consumed by PrivyApiError?

## Target
- File/function: [src/Error.ts](src/Error.ts) - PrivyApiError, PrivyClientError, MoonpayApiError, createErrorFormatter, errorIndicatesMfaCanceled
- Entrypoint: every catch path in the SDK
- Attacker controls: error.code / error.message strings returned by any reachable response
- Exploit idea: Pass a crafted value and inspect the resulting query string.
- Invariant to test: Query values must be primitive and encoded before reaching the URL.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: pass an object with a custom toString to PrivyApiError and assert the query is encoded, not concatenated.
