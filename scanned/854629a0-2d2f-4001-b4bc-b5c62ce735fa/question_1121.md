# Q1121: path params not escaped in InMemoryStorage.ts

## Question
getPath compiles route params with getPathWithParams then appends toSearchParams output; can an attacker pass a param containing slashes or query characters so InMemoryCache.get targets a different endpoint?

## Target
- File/function: [src/storage/InMemoryStorage.ts](src/storage/InMemoryStorage.ts) - InMemoryCache.get, put, del, getKeys (plain object _cache)
- Entrypoint: Privy({storage: new InMemoryCache()})
- Attacker controls: key strings reaching the object literal cache
- Exploit idea: Call a param-taking route with '../' or '?x=' inside the param value.
- Invariant to test: Route parameters must be encoded so they cannot alter the request path.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: call InMemoryCache.get with a param of '../other' and assert the compiled path stays within the intended route.
