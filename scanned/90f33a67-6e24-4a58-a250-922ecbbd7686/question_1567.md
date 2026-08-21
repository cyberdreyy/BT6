# Q1567: getKeys exposes the whole origin in Error.ts

## Question
LocalStorage.getKeys enumerates every key in the origin's localStorage; can an attacker use a path through src/Error.ts to read keys or values written by unrelated code on that origin?

## Target
- File/function: [src/Error.ts](src/Error.ts) - PrivyApiError, PrivyClientError, MoonpayApiError, createErrorFormatter, errorIndicatesMfaCanceled
- Entrypoint: every catch path in the SDK
- Attacker controls: error.code / error.message strings returned by any reachable response
- Exploit idea: Call the storage-enumerating path and inspect what is returned to app code.
- Invariant to test: Storage access from src/Error.ts must be namespaced to privy: keys.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: seed a foreign key and assert PrivyApiError does not return it.
