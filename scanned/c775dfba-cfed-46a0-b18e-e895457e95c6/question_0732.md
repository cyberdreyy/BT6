# Q0732: provider token cached in localStorage in linkWithCrossAppAuth.ts

## Question
CrossAppApi stores the provider access token under privy:cross-app:<appId> in plain storage; can a later unprivileged user of the same profile read it and act as the victim on the provider app after linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode?

## Target
- File/function: [src/action/crossApp/linkWithCrossAppAuth.ts](src/action/crossApp/linkWithCrossAppAuth.ts) - linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode, listener unsubscribed after
- Entrypoint: privy.crossApp.linkWithCrossAppAuth({providerAppId, redirectUrl})
- Attacker controls: providerAppId, redirectUrl, oauth_tokens emitted while the listener is attached
- Exploit idea: Complete a cross-app login, then read the storage key from a fresh context.
- Invariant to test: Provider tokens must be cleared with the session and never persisted in plain storage.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: run linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode, call destroyLocalState and assert the cross-app key is gone.
