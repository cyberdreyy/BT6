# Q2328: storage accessibility probe leaks a key in Token.ts

## Question
isStorageAccessible writes privy:__storage__test-<uuid> before every refresh; can an attacker use the residue or the failure path of Token.parse to influence whether refresh proceeds?

## Target
- File/function: [src/Token.ts](src/Token.ts) - Token.parse, Token.subject/expiration/issuer/audience, isExpired (jose.decodeJwt, no signature verification)
- Entrypoint: Session.getCustomerAccessToken, backfillLegacySession, CrossAppApi.getProviderAccessToken
- Attacker controls: any JWT-shaped string reachable into storage or a cross-app response
- Exploit idea: Make the probe fail transiently and observe the refresh being skipped while credentials remain.
- Invariant to test: A storage probe failure must not silently change session lifecycle decisions.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: make put throw once and assert Token.parse surfaces the error rather than continuing with stale state.
