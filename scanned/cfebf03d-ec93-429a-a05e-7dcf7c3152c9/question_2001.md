# Q2001: appId or clientId swapped at construction in InMemoryStorage.ts

## Question
Privy's constructor accepts appId, clientId, baseUrl, storage and crypto; can an attacker in the page reach InMemoryCache.get with substituted options so requests are signed and stored under a different app namespace?

## Target
- File/function: [src/storage/InMemoryStorage.ts](src/storage/InMemoryStorage.ts) - InMemoryCache.get, put, del, getKeys (plain object _cache)
- Entrypoint: Privy({storage: new InMemoryCache()})
- Attacker controls: key strings reaching the object literal cache
- Exploit idea: Construct a second client with a different appId sharing the same storage and observe key collisions.
- Invariant to test: Storage namespacing must prevent one app id's session from being consumed by another.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: run two clients with different appIds over one Storage and assert no key collisions.
