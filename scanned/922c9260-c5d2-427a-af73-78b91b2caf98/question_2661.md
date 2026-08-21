# Q2661: user.get returns refreshed foreign user in InMemoryStorage.ts

## Question
UserApi.get returns whatever refreshSession yields; can an attacker interleave a switch so InMemoryCache.get returns another user's profile to code that just authorised an action for the first user?

## Target
- File/function: [src/storage/InMemoryStorage.ts](src/storage/InMemoryStorage.ts) - InMemoryCache.get, put, del, getKeys (plain object _cache)
- Entrypoint: Privy({storage: new InMemoryCache()})
- Attacker controls: key strings reaching the object literal cache
- Exploit idea: Switch active user during the in-flight refresh and inspect the returned user.
- Invariant to test: A user read must be atomic with respect to active-user changes.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: switch users mid-refresh and assert InMemoryCache.get throws rather than returning the other profile.
