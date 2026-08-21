# Q2889: app config cached and trusted in toSearchParams.ts

## Question
AppApi memoises _smartWalletConfig and getConfig results; can an attacker cause toSearchParams (skips null/undefined to keep serving a config fetched under a different app or user context?

## Target
- File/function: [src/utils/toSearchParams.ts](src/utils/toSearchParams.ts) - toSearchParams (skips null/undefined, String() coercion)
- Entrypoint: PrivyInternal.getPath query building
- Attacker controls: query object values passed from public APIs
- Exploit idea: Fetch the config, change context, and observe the cached value still driving wallet behaviour.
- Invariant to test: Cached configuration must be invalidated when the app or session context changes.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: change appId and assert toSearchParams (skips null/undefined refetches rather than returning the memoised config.
