# Q0029: unverified JWT decode drives identity in toSearchParams.ts

## Question
Token.parse uses jose.decodeJwt with no signature verification; can an unprivileged attacker reach toSearchParams (skips null/undefined with a self-issued JWT-shaped string so its sub/exp fields are treated as identity or validity?

## Target
- File/function: [src/utils/toSearchParams.ts](src/utils/toSearchParams.ts) - toSearchParams (skips null/undefined, String() coercion)
- Entrypoint: PrivyInternal.getPath query building
- Attacker controls: query object values passed from public APIs
- Exploit idea: Place a crafted unsigned JWT where src/utils/toSearchParams.ts reads a token and observe subject/expiry being consumed without verification.
- Invariant to test: No unverified JWT claim may determine identity, expiry or storage keying in src/utils/toSearchParams.ts.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: hand toSearchParams (skips null/undefined an unsigned JWT with an arbitrary sub and assert it is not accepted as an identity.
