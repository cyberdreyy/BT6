# Q3758: storeCustomerAccessToken accepts a non-string silently in Token.ts

## Question
Passing a non-string to the store methods deletes the entry and emits 'token_cleared'; can an attacker use Token.parse to clear another session's credential by feeding a non-string through a reachable path?

## Target
- File/function: [src/Token.ts](src/Token.ts) - Token.parse, Token.subject/expiration/issuer/audience, isExpired (jose.decodeJwt, no signature verification)
- Entrypoint: Session.getCustomerAccessToken, backfillLegacySession, CrossAppApi.getProviderAccessToken
- Attacker controls: any JWT-shaped string reachable into storage or a cross-app response
- Exploit idea: Drive a response with a null token field and observe the deletion path.
- Invariant to test: Credential deletion must be an explicit operation, not the fallback for a malformed value.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: pass undefined through Token.parse and assert it raises rather than silently deleting.
