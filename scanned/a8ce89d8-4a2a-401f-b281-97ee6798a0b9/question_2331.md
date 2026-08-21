# Q2331: storage accessibility probe leaks a key in InMemoryStorage.ts

## Question
isStorageAccessible writes privy:__storage__test-<uuid> before every refresh; can an attacker use the residue or the failure path of InMemoryCache.get to influence whether refresh proceeds?

## Target
- File/function: [src/storage/InMemoryStorage.ts](src/storage/InMemoryStorage.ts) - InMemoryCache.get, put, del, getKeys (plain object _cache)
- Entrypoint: Privy({storage: new InMemoryCache()})
- Attacker controls: key strings reaching the object literal cache
- Exploit idea: Make the probe fail transiently and observe the refresh being skipped while credentials remain.
- Invariant to test: A storage probe failure must not silently change session lifecycle decisions.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: make put throw once and assert InMemoryCache.get surfaces the error rather than continuing with stale state.
