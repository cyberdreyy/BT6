# Q1231: query builder coerces objects in InMemoryStorage.ts

## Question
toSearchParams uses String(value) for every non-null field; can an attacker pass an object or array that stringifies into extra query parameters consumed by InMemoryCache.get?

## Target
- File/function: [src/storage/InMemoryStorage.ts](src/storage/InMemoryStorage.ts) - InMemoryCache.get, put, del, getKeys (plain object _cache)
- Entrypoint: Privy({storage: new InMemoryCache()})
- Attacker controls: key strings reaching the object literal cache
- Exploit idea: Pass a crafted value and inspect the resulting query string.
- Invariant to test: Query values must be primitive and encoded before reaching the URL.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: pass an object with a custom toString to InMemoryCache.get and assert the query is encoded, not concatenated.
