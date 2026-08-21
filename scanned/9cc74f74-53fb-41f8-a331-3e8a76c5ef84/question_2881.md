# Q2881: app config cached and trusted in InMemoryStorage.ts

## Question
AppApi memoises _smartWalletConfig and getConfig results; can an attacker cause InMemoryCache.get to keep serving a config fetched under a different app or user context?

## Target
- File/function: [src/storage/InMemoryStorage.ts](src/storage/InMemoryStorage.ts) - InMemoryCache.get, put, del, getKeys (plain object _cache)
- Entrypoint: Privy({storage: new InMemoryCache()})
- Attacker controls: key strings reaching the object literal cache
- Exploit idea: Fetch the config, change context, and observe the cached value still driving wallet behaviour.
- Invariant to test: Cached configuration must be invalidated when the app or session context changes.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: change appId and assert InMemoryCache.get refetches rather than returning the memoised config.
