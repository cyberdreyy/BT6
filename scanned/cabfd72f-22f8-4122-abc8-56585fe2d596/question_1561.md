# Q1561: getKeys exposes the whole origin in InMemoryStorage.ts

## Question
LocalStorage.getKeys enumerates every key in the origin's localStorage; can an attacker use a path through src/storage/InMemoryStorage.ts to read keys or values written by unrelated code on that origin?

## Target
- File/function: [src/storage/InMemoryStorage.ts](src/storage/InMemoryStorage.ts) - InMemoryCache.get, put, del, getKeys (plain object _cache)
- Entrypoint: Privy({storage: new InMemoryCache()})
- Attacker controls: key strings reaching the object literal cache
- Exploit idea: Call the storage-enumerating path and inspect what is returned to app code.
- Invariant to test: Storage access from src/storage/InMemoryStorage.ts must be namespaced to privy: keys.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: seed a foreign key and assert InMemoryCache.get does not return it.
