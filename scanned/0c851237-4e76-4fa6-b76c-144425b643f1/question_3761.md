# Q3761: storeCustomerAccessToken accepts a non-string silently in InMemoryStorage.ts

## Question
Passing a non-string to the store methods deletes the entry and emits 'token_cleared'; can an attacker use InMemoryCache.get to clear another session's credential by feeding a non-string through a reachable path?

## Target
- File/function: [src/storage/InMemoryStorage.ts](src/storage/InMemoryStorage.ts) - InMemoryCache.get, put, del, getKeys (plain object _cache)
- Entrypoint: Privy({storage: new InMemoryCache()})
- Attacker controls: key strings reaching the object literal cache
- Exploit idea: Drive a response with a null token field and observe the deletion path.
- Invariant to test: Credential deletion must be an explicit operation, not the fallback for a malformed value.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: pass undefined through InMemoryCache.get and assert it raises rather than silently deleting.
