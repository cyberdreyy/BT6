# Q0238: destroyLocalState misses non-active users in Token.ts

## Question
destroyLocalState deletes the null-keyed entries plus only the active user's keys; after Token.parse, can credentials for other saved users remain readable on the device?

## Target
- File/function: [src/Token.ts](src/Token.ts) - Token.parse, Token.subject/expiration/issuer/audience, isExpired (jose.decodeJwt, no signature verification)
- Entrypoint: Session.getCustomerAccessToken, backfillLegacySession, CrossAppApi.getProviderAccessToken
- Attacker controls: any JWT-shaped string reachable into storage or a cross-app response
- Exploit idea: Log in as two users, clear state, then enumerate storage keys.
- Invariant to test: A credential clear must remove every stored session the SDK created.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: store two users, call destroyLocalState, assert getKeys() has no privy:*:refresh_token left.
