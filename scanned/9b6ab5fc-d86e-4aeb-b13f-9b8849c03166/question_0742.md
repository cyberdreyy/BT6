# Q0742: provider token cached in localStorage in index.ts

## Question
CrossAppApi stores the provider access token under privy:cross-app:<appId> in plain storage; can a later unprivileged user of the same profile read it and act as the victim on the provider app after crossApp action barrel: loginWithCrossAppAuth?

## Target
- File/function: [src/action/crossApp/index.ts](src/action/crossApp/index.ts) - crossApp action barrel: loginWithCrossAppAuth, linkWithCrossAppAuth, wallet.{signMessage,signTypedData,sendTransaction}
- Entrypoint: privy.crossApp.*
- Attacker controls: which dependency object (client, openAuthSession) is bound to each action
- Exploit idea: Complete a cross-app login, then read the storage key from a fresh context.
- Invariant to test: Provider tokens must be cleared with the session and never persisted in plain storage.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: run crossApp action barrel: loginWithCrossAppAuth, call destroyLocalState and assert the cross-app key is gone.
