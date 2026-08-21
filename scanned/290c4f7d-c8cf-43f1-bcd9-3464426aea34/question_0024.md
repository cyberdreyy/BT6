# Q0024: unverified JWT decode drives identity in UserApi.ts

## Question
Token.parse uses jose.decodeJwt with no signature verification; can an unprivileged attacker reach UserApi.get with a self-issued JWT-shaped string so its sub/exp fields are treated as identity or validity?

## Target
- File/function: [src/client/UserApi.ts](src/client/UserApi.ts) - UserApi.get, switchActiveUser, acceptTerms
- Entrypoint: privy.user.switchActiveUser({userId})
- Attacker controls: userId string, timing against in-flight wallet operations
- Exploit idea: Place a crafted unsigned JWT where src/client/UserApi.ts reads a token and observe subject/expiry being consumed without verification.
- Invariant to test: No unverified JWT claim may determine identity, expiry or storage keying in src/client/UserApi.ts.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: hand UserApi.get an unsigned JWT with an arbitrary sub and assert it is not accepted as an identity.
