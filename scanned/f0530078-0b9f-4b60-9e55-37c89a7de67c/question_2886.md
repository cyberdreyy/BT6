# Q2886: app config cached and trusted in logger.ts

## Question
AppApi memoises _smartWalletConfig and getConfig results; can an attacker cause logger levels NONE/ERROR/WARN/INFO/DEBUG to keep serving a config fetched under a different app or user context?

## Target
- File/function: [src/client/logger.ts](src/client/logger.ts) - logger levels NONE/ERROR/WARN/INFO/DEBUG, privy:refresh debug lines
- Entrypoint: new Privy({logLevel: 'DEBUG'})
- Attacker controls: what the SDK writes to console at each level
- Exploit idea: Fetch the config, change context, and observe the cached value still driving wallet behaviour.
- Invariant to test: Cached configuration must be invalidated when the app or session context changes.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: change appId and assert logger levels NONE/ERROR/WARN/INFO/DEBUG refetches rather than returning the memoised config.
