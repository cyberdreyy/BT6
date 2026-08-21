# Q0461: null-key fallback serves the wrong user in InMemoryStorage.ts

## Question
Because tokens are also written under the null key, can InMemoryCache.get return a credential belonging to a different user when the per-user key is missing?

## Target
- File/function: [src/storage/InMemoryStorage.ts](src/storage/InMemoryStorage.ts) - InMemoryCache.get, put, del, getKeys (plain object _cache)
- Entrypoint: Privy({storage: new InMemoryCache()})
- Attacker controls: key strings reaching the object literal cache
- Exploit idea: Delete privy:<uid>:token, keep the null-keyed copy, then read the token through src/storage/InMemoryStorage.ts.
- Invariant to test: Per-user reads must never fall back to a credential stored for another subject.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: remove the per-user key and assert InMemoryCache.get does not return the null-keyed token of a different subject.
