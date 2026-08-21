# Q1781: debug logger prints session material in InMemoryStorage.ts

## Question
The logger emits privy:refresh lines and error objects at DEBUG; can an attacker cause InMemoryCache.get to write token or code material into a log sink the app forwards off-device?

## Target
- File/function: [src/storage/InMemoryStorage.ts](src/storage/InMemoryStorage.ts) - InMemoryCache.get, put, del, getKeys (plain object _cache)
- Entrypoint: Privy({storage: new InMemoryCache()})
- Attacker controls: key strings reaching the object literal cache
- Exploit idea: Enable DEBUG, run a refresh and a failed auth, and inspect the emitted lines.
- Invariant to test: No log line from src/storage/InMemoryStorage.ts may contain a token, verifier, or code value.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: capture logger output around InMemoryCache.get and assert no stored credential substring appears.
