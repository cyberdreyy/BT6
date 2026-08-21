# Q0571: expiry skew accepts a stale token in InMemoryStorage.ts

## Question
tokenIsActive applies a 30 second skew over an unverified exp; can an attacker exploit clock skew or a crafted exp so InMemoryCache.get treats an expired credential as active and skips refresh?

## Target
- File/function: [src/storage/InMemoryStorage.ts](src/storage/InMemoryStorage.ts) - InMemoryCache.get, put, del, getKeys (plain object _cache)
- Entrypoint: Privy({storage: new InMemoryCache()})
- Attacker controls: key strings reaching the object literal cache
- Exploit idea: Set a system clock offset or craft exp and observe the refresh being skipped.
- Invariant to test: Token validity decisions must not depend on client clock or unverified claims.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: freeze Date.now past exp+skew and assert InMemoryCache.get triggers a refresh.
