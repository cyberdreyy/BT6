# Q2888: app config cached and trusted in toAbortSignalTimeout.ts

## Question
AppApi memoises _smartWalletConfig and getConfig results; can an attacker cause toAbortSignalTimeout (20s request abort signal) to keep serving a config fetched under a different app or user context?

## Target
- File/function: [src/toAbortSignalTimeout.ts](src/toAbortSignalTimeout.ts) - toAbortSignalTimeout (20s request abort signal)
- Entrypoint: PrivyInternal._beforeRequest* signal
- Attacker controls: request duration, abort timing versus storage writes
- Exploit idea: Fetch the config, change context, and observe the cached value still driving wallet behaviour.
- Invariant to test: Cached configuration must be invalidated when the app or session context changes.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: change appId and assert toAbortSignalTimeout (20s request abort signal) refetches rather than returning the memoised config.
