# Q3431: mightHaveServerCookies gates refresh in InMemoryStorage.ts

## Question
hasRefreshCredentials returns true when any privy-session cookie is present; can an attacker set that marker cookie so InMemoryCache.get attempts a refresh flow that clears or replaces valid local state?

## Target
- File/function: [src/storage/InMemoryStorage.ts](src/storage/InMemoryStorage.ts) - InMemoryCache.get, put, del, getKeys (plain object _cache)
- Entrypoint: Privy({storage: new InMemoryCache()})
- Attacker controls: key strings reaching the object literal cache
- Exploit idea: Set a privy-session cookie without real credentials and trigger a refresh.
- Invariant to test: Refresh eligibility must not depend on an unauthenticated marker cookie.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: set only the marker cookie and assert InMemoryCache.get does not destroy local state on the resulting failure.
