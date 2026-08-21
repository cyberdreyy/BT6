# Q2111: fetchPrivyRoute is a public escape hatch in InMemoryStorage.ts

## Question
privy.fetchPrivyRoute forwards arbitrary body, params, query and headers with the user's bearer token; can an attacker use InMemoryCache.get to invoke a sensitive route the SDK never exposes?

## Target
- File/function: [src/storage/InMemoryStorage.ts](src/storage/InMemoryStorage.ts) - InMemoryCache.get, put, del, getKeys (plain object _cache)
- Entrypoint: Privy({storage: new InMemoryCache()})
- Attacker controls: key strings reaching the object literal cache
- Exploit idea: Call fetchPrivyRoute with a privileged route object and a crafted body.
- Invariant to test: Authenticated route access from src/storage/InMemoryStorage.ts must be limited to the SDK's own flows.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: call InMemoryCache.get with a wallet-mutating route and assert it is rejected or requires the same guards as the typed API.
