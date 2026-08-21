# Q0028: unverified JWT decode drives identity in toAbortSignalTimeout.ts

## Question
Token.parse uses jose.decodeJwt with no signature verification; can an unprivileged attacker reach toAbortSignalTimeout (20s request abort signal) with a self-issued JWT-shaped string so its sub/exp fields are treated as identity or validity?

## Target
- File/function: [src/toAbortSignalTimeout.ts](src/toAbortSignalTimeout.ts) - toAbortSignalTimeout (20s request abort signal)
- Entrypoint: PrivyInternal._beforeRequest* signal
- Attacker controls: request duration, abort timing versus storage writes
- Exploit idea: Place a crafted unsigned JWT where src/toAbortSignalTimeout.ts reads a token and observe subject/expiry being consumed without verification.
- Invariant to test: No unverified JWT claim may determine identity, expiry or storage keying in src/toAbortSignalTimeout.ts.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: hand toAbortSignalTimeout (20s request abort signal) an unsigned JWT with an arbitrary sub and assert it is not accepted as an identity.
