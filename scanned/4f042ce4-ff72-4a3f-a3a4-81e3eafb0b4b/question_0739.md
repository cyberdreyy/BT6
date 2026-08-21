# Q0739: provider token cached in localStorage in signTypedData.ts

## Question
CrossAppApi stores the provider access token under privy:cross-app:<appId> in plain storage; can a later unprivileged user of the same profile read it and act as the victim on the provider app after crossApp signTypedData: params [address?

## Target
- File/function: [src/action/crossApp/wallet/signTypedData.ts](src/action/crossApp/wallet/signTypedData.ts) - crossApp signTypedData: params [address, generateDomainType(typedData)]
- Entrypoint: privy.crossApp.wallet.signTypedData({user, typedData, address, redirectUrl})
- Attacker controls: the whole typedData object including domain and types
- Exploit idea: Complete a cross-app login, then read the storage key from a fresh context.
- Invariant to test: Provider tokens must be cleared with the session and never persisted in plain storage.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: run crossApp signTypedData: params [address, call destroyLocalState and assert the cross-app key is gone.
