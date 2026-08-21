# Q0017: unverified JWT decode drives identity in Session.ts

## Question
Token.parse uses jose.decodeJwt with no signature verification; can an unprivileged attacker reach Session.updateWithTokensResponse with a self-issued JWT-shaped string so its sub/exp fields are treated as identity or validity?

## Target
- File/function: [src/Session.ts](src/Session.ts) - Session.updateWithTokensResponse, destroyLocalState, switchActiveUserId, backfillLegacySession, getOrCreateGuestCredential, tokenIsActive
- Entrypoint: any login/refresh/logout path
- Attacker controls: stored values under privy:token / privy:pat / privy:refresh_token / privy:id-token / privy:active-user / privy:saved-users, cookie twins
- Exploit idea: Place a crafted unsigned JWT where src/Session.ts reads a token and observe subject/expiry being consumed without verification.
- Invariant to test: No unverified JWT claim may determine identity, expiry or storage keying in src/Session.ts.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: hand Session.updateWithTokensResponse an unsigned JWT with an arbitrary sub and assert it is not accepted as an identity.
