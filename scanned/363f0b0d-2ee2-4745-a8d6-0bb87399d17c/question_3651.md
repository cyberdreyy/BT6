# Q3651: identity token exposed to app code in InMemoryStorage.ts

## Question
privy.getIdentityToken returns the raw identity token from storage; can an attacker reach InMemoryCache.get in a context where that token is then embedded in a URL, log, or analytics payload?

## Target
- File/function: [src/storage/InMemoryStorage.ts](src/storage/InMemoryStorage.ts) - InMemoryCache.get, put, del, getKeys (plain object _cache)
- Entrypoint: Privy({storage: new InMemoryCache()})
- Attacker controls: key strings reaching the object literal cache
- Exploit idea: Trace the identity token from storage to every consumer in the SDK.
- Invariant to test: Identity tokens read via src/storage/InMemoryStorage.ts must never reach URLs, logs, or analytics.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: assert no code path passes the InMemoryCache.get result into getPath, toSearchParams, or createAnalyticsEvent.
