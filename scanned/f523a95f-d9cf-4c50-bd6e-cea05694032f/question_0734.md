# Q0734: provider token cached in localStorage in getProviderAccessTokenOrRelink.ts

## Question
CrossAppApi stores the provider access token under privy:cross-app:<appId> in plain storage; can a later unprivileged user of the same profile read it and act as the victim on the provider app after getProviderAccessTokenOrRelink: cached token from storage else relink?

## Target
- File/function: [src/action/crossApp/wallet/utils/getProviderAccessTokenOrRelink.ts](src/action/crossApp/wallet/utils/getProviderAccessTokenOrRelink.ts) - getProviderAccessTokenOrRelink: cached token from storage else relink
- Entrypoint: cross-app wallet operations
- Attacker controls: the cached privy:cross-app:<appId> value and its decoded expiry
- Exploit idea: Complete a cross-app login, then read the storage key from a fresh context.
- Invariant to test: Provider tokens must be cleared with the session and never persisted in plain storage.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: run getProviderAccessTokenOrRelink: cached token from storage else relink, call destroyLocalState and assert the cross-app key is gone.
