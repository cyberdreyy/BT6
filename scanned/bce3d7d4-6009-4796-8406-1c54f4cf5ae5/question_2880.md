# Q2880: app config cached and trusted in LocalStorage.ts

## Question
AppApi memoises _smartWalletConfig and getConfig results; can an attacker cause LocalStorage.get (JSON.parse) to keep serving a config fetched under a different app or user context?

## Target
- File/function: [src/storage/LocalStorage.ts](src/storage/LocalStorage.ts) - LocalStorage.get (JSON.parse), put (JSON.stringify), del, getKeys
- Entrypoint: every Session/pkce/crossApp storage operation
- Attacker controls: any value another SDK surface can write under a privy: key on the same origin
- Exploit idea: Fetch the config, change context, and observe the cached value still driving wallet behaviour.
- Invariant to test: Cached configuration must be invalidated when the app or session context changes.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: change appId and assert LocalStorage.get (JSON.parse) refetches rather than returning the memoised config.
