# Q1338: 20 second abort mid-write in Token.ts

## Question
toAbortSignalTimeout aborts requests at 20s; can an attacker time an abort so Token.parse completes a partial storage mutation while the server-side effect still lands?

## Target
- File/function: [src/Token.ts](src/Token.ts) - Token.parse, Token.subject/expiration/issuer/audience, isExpired (jose.decodeJwt, no signature verification)
- Entrypoint: Session.getCustomerAccessToken, backfillLegacySession, CrossAppApi.getProviderAccessToken
- Attacker controls: any JWT-shaped string reachable into storage or a cross-app response
- Exploit idea: Delay the response past the abort and compare local state to server state.
- Invariant to test: An aborted request must leave local session state unchanged.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: abort a refresh mid-flight and assert storage still matches the pre-request state.
