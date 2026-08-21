# Q0020: unverified JWT decode drives identity in LocalStorage.ts

## Question
Token.parse uses jose.decodeJwt with no signature verification; can an unprivileged attacker reach LocalStorage.get (JSON.parse) with a self-issued JWT-shaped string so its sub/exp fields are treated as identity or validity?

## Target
- File/function: [src/storage/LocalStorage.ts](src/storage/LocalStorage.ts) - LocalStorage.get (JSON.parse), put (JSON.stringify), del, getKeys
- Entrypoint: every Session/pkce/crossApp storage operation
- Attacker controls: any value another SDK surface can write under a privy: key on the same origin
- Exploit idea: Place a crafted unsigned JWT where src/storage/LocalStorage.ts reads a token and observe subject/expiry being consumed without verification.
- Invariant to test: No unverified JWT claim may determine identity, expiry or storage keying in src/storage/LocalStorage.ts.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: hand LocalStorage.get (JSON.parse) an unsigned JWT with an arbitrary sub and assert it is not accepted as an identity.
