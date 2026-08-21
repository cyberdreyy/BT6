# Q3208: key builder collides on crafted user ids in Token.ts

## Question
Token storage keys are built by string interpolation of the user id; can an attacker obtain or seed a user id containing ':' so keys for two users collide?

## Target
- File/function: [src/Token.ts](src/Token.ts) - Token.parse, Token.subject/expiration/issuer/audience, isExpired (jose.decodeJwt, no signature verification)
- Entrypoint: Session.getCustomerAccessToken, backfillLegacySession, CrossAppApi.getProviderAccessToken
- Attacker controls: any JWT-shaped string reachable into storage or a cross-app response
- Exploit idea: Store sessions for ids 'a' and 'a:token' style values and compare resulting keys.
- Invariant to test: Key construction in src/Token.ts must be injective over user ids.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: assert Token.parse produces distinct keys for ids that differ only by separators.
