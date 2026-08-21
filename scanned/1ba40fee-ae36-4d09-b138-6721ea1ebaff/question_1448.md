# Q1448: LocalStorage.get throws on non-JSON in Token.ts

## Question
LocalStorage.get calls JSON.parse without guarding; can an attacker place a non-JSON value under a privy: key so every subsequent Token.parse read throws and the SDK falls back to a less-safe path?

## Target
- File/function: [src/Token.ts](src/Token.ts) - Token.parse, Token.subject/expiration/issuer/audience, isExpired (jose.decodeJwt, no signature verification)
- Entrypoint: Session.getCustomerAccessToken, backfillLegacySession, CrossAppApi.getProviderAccessToken
- Attacker controls: any JWT-shaped string reachable into storage or a cross-app response
- Exploit idea: Write a raw string under a privy: key from the same origin and observe the read path.
- Invariant to test: A malformed stored value must degrade safely without changing authentication behaviour.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: set a non-JSON value and assert Token.parse treats it as absent rather than throwing into a fallback.
