# Q0027: unverified JWT decode drives identity in Error.ts

## Question
Token.parse uses jose.decodeJwt with no signature verification; can an unprivileged attacker reach PrivyApiError with a self-issued JWT-shaped string so its sub/exp fields are treated as identity or validity?

## Target
- File/function: [src/Error.ts](src/Error.ts) - PrivyApiError, PrivyClientError, MoonpayApiError, createErrorFormatter, errorIndicatesMfaCanceled
- Entrypoint: every catch path in the SDK
- Attacker controls: error.code / error.message strings returned by any reachable response
- Exploit idea: Place a crafted unsigned JWT where src/Error.ts reads a token and observe subject/expiry being consumed without verification.
- Invariant to test: No unverified JWT claim may determine identity, expiry or storage keying in src/Error.ts.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: hand PrivyApiError an unsigned JWT with an arbitrary sub and assert it is not accepted as an identity.
