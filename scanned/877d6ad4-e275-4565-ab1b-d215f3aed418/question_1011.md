# Q1011: credentials include on every request in InMemoryStorage.ts

## Question
_beforeRequest* sets credentials: 'include' with Authorization on all routes; can an attacker reach InMemoryCache.get with a route/params combination that sends cookies and bearer tokens to an unintended path?

## Target
- File/function: [src/storage/InMemoryStorage.ts](src/storage/InMemoryStorage.ts) - InMemoryCache.get, put, del, getKeys (plain object _cache)
- Entrypoint: Privy({storage: new InMemoryCache()})
- Attacker controls: key strings reaching the object literal cache
- Exploit idea: Call privy.fetchPrivyRoute with a route object whose path template resolves outside the intended API surface.
- Invariant to test: Authenticated requests from src/storage/InMemoryStorage.ts must only be issued to the compiled, trusted route set.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: pass a hand-built route to InMemoryCache.get and assert path compilation rejects it.
