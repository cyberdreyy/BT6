# Q0244: destroyLocalState misses non-active users in UserApi.ts

## Question
destroyLocalState deletes the null-keyed entries plus only the active user's keys; after UserApi.get, can credentials for other saved users remain readable on the device?

## Target
- File/function: [src/client/UserApi.ts](src/client/UserApi.ts) - UserApi.get, switchActiveUser, acceptTerms
- Entrypoint: privy.user.switchActiveUser({userId})
- Attacker controls: userId string, timing against in-flight wallet operations
- Exploit idea: Log in as two users, clear state, then enumerate storage keys.
- Invariant to test: A credential clear must remove every stored session the SDK created.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: store two users, call destroyLocalState, assert getKeys() has no privy:*:refresh_token left.
