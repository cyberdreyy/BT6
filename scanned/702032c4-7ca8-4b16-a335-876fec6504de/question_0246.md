# Q0246: destroyLocalState misses non-active users in logger.ts

## Question
destroyLocalState deletes the null-keyed entries plus only the active user's keys; after logger levels NONE/ERROR/WARN/INFO/DEBUG, can credentials for other saved users remain readable on the device?

## Target
- File/function: [src/client/logger.ts](src/client/logger.ts) - logger levels NONE/ERROR/WARN/INFO/DEBUG, privy:refresh debug lines
- Entrypoint: new Privy({logLevel: 'DEBUG'})
- Attacker controls: what the SDK writes to console at each level
- Exploit idea: Log in as two users, clear state, then enumerate storage keys.
- Invariant to test: A credential clear must remove every stored session the SDK created.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: store two users, call destroyLocalState, assert getKeys() has no privy:*:refresh_token left.
