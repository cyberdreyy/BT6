# Q0128: backfill trusts the token subject in Token.ts

## Question
Session.backfillLegacySession derives the user id from Token.parse(token).subject of a legacy null-keyed value; can an attacker seed that key so Token.parse adopts an attacker-chosen user id as the active user?

## Target
- File/function: [src/Token.ts](src/Token.ts) - Token.parse, Token.subject/expiration/issuer/audience, isExpired (jose.decodeJwt, no signature verification)
- Entrypoint: Session.getCustomerAccessToken, backfillLegacySession, CrossAppApi.getProviderAccessToken
- Attacker controls: any JWT-shaped string reachable into storage or a cross-app response
- Exploit idea: Write a crafted legacy token, initialize the SDK in multi-user mode and read privy:active-user.
- Invariant to test: The active user id must be derived from a server-verified session, not from a locally stored token body.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: seed privy:token with an unsigned JWT and assert backfill does not set privy:active-user from it.
