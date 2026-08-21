# Q2884: app config cached and trusted in UserApi.ts

## Question
AppApi memoises _smartWalletConfig and getConfig results; can an attacker cause UserApi.get to keep serving a config fetched under a different app or user context?

## Target
- File/function: [src/client/UserApi.ts](src/client/UserApi.ts) - UserApi.get, switchActiveUser, acceptTerms
- Entrypoint: privy.user.switchActiveUser({userId})
- Attacker controls: userId string, timing against in-flight wallet operations
- Exploit idea: Fetch the config, change context, and observe the cached value still driving wallet behaviour.
- Invariant to test: Cached configuration must be invalidated when the app or session context changes.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: change appId and assert UserApi.get refetches rather than returning the memoised config.
