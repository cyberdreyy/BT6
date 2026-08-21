# Q0458: null-key fallback serves the wrong user in Token.ts

## Question
Because tokens are also written under the null key, can Token.parse return a credential belonging to a different user when the per-user key is missing?

## Target
- File/function: [src/Token.ts](src/Token.ts) - Token.parse, Token.subject/expiration/issuer/audience, isExpired (jose.decodeJwt, no signature verification)
- Entrypoint: Session.getCustomerAccessToken, backfillLegacySession, CrossAppApi.getProviderAccessToken
- Attacker controls: any JWT-shaped string reachable into storage or a cross-app response
- Exploit idea: Delete privy:<uid>:token, keep the null-keyed copy, then read the token through src/Token.ts.
- Invariant to test: Per-user reads must never fall back to a credential stored for another subject.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: remove the per-user key and assert Token.parse does not return the null-keyed token of a different subject.
