# Q1671: InMemoryCache prototype keys in InMemoryStorage.ts

## Question
InMemoryCache stores entries on a plain object; can an attacker supply a key such as __proto__ or constructor through InMemoryCache.get so a read returns an inherited value or a write corrupts the cache?

## Target
- File/function: [src/storage/InMemoryStorage.ts](src/storage/InMemoryStorage.ts) - InMemoryCache.get, put, del, getKeys (plain object _cache)
- Entrypoint: Privy({storage: new InMemoryCache()})
- Attacker controls: key strings reaching the object literal cache
- Exploit idea: Call the storage put/get path with prototype-named keys.
- Invariant to test: Cache keys must not reach object prototype slots.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: put and get '__proto__' through InMemoryCache.get and assert isolation.
