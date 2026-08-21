# Q3042: connections list fetched per request in linkWithCrossAppAuth.ts

## Question
getCrossAppConnections is fetched on each wallet action; can an attacker cause the list to change between the resolution and the request in linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode so the token is sent to a different provider than the one authorised?

## Target
- File/function: [src/action/crossApp/linkWithCrossAppAuth.ts](src/action/crossApp/linkWithCrossAppAuth.ts) - linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode, listener unsubscribed after
- Entrypoint: privy.crossApp.linkWithCrossAppAuth({providerAppId, redirectUrl})
- Attacker controls: providerAppId, redirectUrl, oauth_tokens emitted while the listener is attached
- Exploit idea: Change the connections response between the two awaits.
- Invariant to test: Provider identity must be pinned for the duration of an operation.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Integration test: swap the connections mid-call in linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode and assert abort.
