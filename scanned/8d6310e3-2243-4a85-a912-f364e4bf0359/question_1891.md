# Q1891: error objects carry response bodies in InMemoryStorage.ts

## Question
PrivyApiError/MoonpayApiError keep code, status and even the raw response; can an attacker surface a thrown error from InMemoryCache.get whose payload leaks another user's data or a credential?

## Target
- File/function: [src/storage/InMemoryStorage.ts](src/storage/InMemoryStorage.ts) - InMemoryCache.get, put, del, getKeys (plain object _cache)
- Entrypoint: Privy({storage: new InMemoryCache()})
- Attacker controls: key strings reaching the object literal cache
- Exploit idea: Force an error response containing sensitive fields and inspect the thrown object reaching app code.
- Invariant to test: Errors raised from src/storage/InMemoryStorage.ts must not carry raw response bodies to app code.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: throw from a route with a sensitive body and assert the error exposes only code and message.
