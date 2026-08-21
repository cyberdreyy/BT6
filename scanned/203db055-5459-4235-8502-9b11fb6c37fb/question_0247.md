# Q0247: destroyLocalState misses non-active users in Error.ts

## Question
destroyLocalState deletes the null-keyed entries plus only the active user's keys; after PrivyApiError, can credentials for other saved users remain readable on the device?

## Target
- File/function: [src/Error.ts](src/Error.ts) - PrivyApiError, PrivyClientError, MoonpayApiError, createErrorFormatter, errorIndicatesMfaCanceled
- Entrypoint: every catch path in the SDK
- Attacker controls: error.code / error.message strings returned by any reachable response
- Exploit idea: Log in as two users, clear state, then enumerate storage keys.
- Invariant to test: A credential clear must remove every stored session the SDK created.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: store two users, call destroyLocalState, assert getKeys() has no privy:*:refresh_token left.
