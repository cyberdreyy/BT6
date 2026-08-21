# Q0026: unverified JWT decode drives identity in logger.ts

## Question
Token.parse uses jose.decodeJwt with no signature verification; can an unprivileged attacker reach logger levels NONE/ERROR/WARN/INFO/DEBUG with a self-issued JWT-shaped string so its sub/exp fields are treated as identity or validity?

## Target
- File/function: [src/client/logger.ts](src/client/logger.ts) - logger levels NONE/ERROR/WARN/INFO/DEBUG, privy:refresh debug lines
- Entrypoint: new Privy({logLevel: 'DEBUG'})
- Attacker controls: what the SDK writes to console at each level
- Exploit idea: Place a crafted unsigned JWT where src/client/logger.ts reads a token and observe subject/expiry being consumed without verification.
- Invariant to test: No unverified JWT claim may determine identity, expiry or storage keying in src/client/logger.ts.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: hand logger levels NONE/ERROR/WARN/INFO/DEBUG an unsigned JWT with an arbitrary sub and assert it is not accepted as an identity.
