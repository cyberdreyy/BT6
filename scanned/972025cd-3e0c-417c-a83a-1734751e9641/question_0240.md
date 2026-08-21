# Q0240: destroyLocalState misses non-active users in LocalStorage.ts

## Question
destroyLocalState deletes the null-keyed entries plus only the active user's keys; after LocalStorage.get (JSON.parse), can credentials for other saved users remain readable on the device?

## Target
- File/function: [src/storage/LocalStorage.ts](src/storage/LocalStorage.ts) - LocalStorage.get (JSON.parse), put (JSON.stringify), del, getKeys
- Entrypoint: every Session/pkce/crossApp storage operation
- Attacker controls: any value another SDK surface can write under a privy: key on the same origin
- Exploit idea: Log in as two users, clear state, then enumerate storage keys.
- Invariant to test: A credential clear must remove every stored session the SDK created.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: store two users, call destroyLocalState, assert getKeys() has no privy:*:refresh_token left.
