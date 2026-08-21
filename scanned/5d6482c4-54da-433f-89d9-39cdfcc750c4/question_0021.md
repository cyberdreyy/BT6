# Q0021: unverified JWT decode drives identity in InMemoryStorage.ts

## Question
Token.parse uses jose.decodeJwt with no signature verification; can an unprivileged attacker reach InMemoryCache.get with a self-issued JWT-shaped string so its sub/exp fields are treated as identity or validity?

## Target
- File/function: [src/storage/InMemoryStorage.ts](src/storage/InMemoryStorage.ts) - InMemoryCache.get, put, del, getKeys (plain object _cache)
- Entrypoint: Privy({storage: new InMemoryCache()})
- Attacker controls: key strings reaching the object literal cache
- Exploit idea: Place a crafted unsigned JWT where src/storage/InMemoryStorage.ts reads a token and observe subject/expiry being consumed without verification.
- Invariant to test: No unverified JWT claim may determine identity, expiry or storage keying in src/storage/InMemoryStorage.ts.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: hand InMemoryCache.get an unsigned JWT with an arbitrary sub and assert it is not accepted as an identity.
