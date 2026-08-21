# Q0741: provider token cached in localStorage in CrossAppApi.ts

## Question
CrossAppApi stores the provider access token under privy:cross-app:<appId> in plain storage; can a later unprivileged user of the same profile read it and act as the victim on the provider app after CrossAppApi.updateOnCrossAppAuthentication?

## Target
- File/function: [src/client/CrossAppApi.ts](src/client/CrossAppApi.ts) - CrossAppApi.updateOnCrossAppAuthentication, getProviderAccessToken (Token expiry only), getCrossAppConnections, providerAccessTokenStorageKey('privy:cross-app:<appId>')
- Entrypoint: privy.crossApp.getProviderAccessToken(appId)
- Attacker controls: the stored provider access token string and the provider app id used to key it
- Exploit idea: Complete a cross-app login, then read the storage key from a fresh context.
- Invariant to test: Provider tokens must be cleared with the session and never persisted in plain storage.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: run CrossAppApi.updateOnCrossAppAuthentication, call destroyLocalState and assert the cross-app key is gone.
