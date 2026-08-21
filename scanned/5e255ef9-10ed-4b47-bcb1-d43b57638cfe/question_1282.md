# Q1282: listener not unsubscribed on failure in linkWithCrossAppAuth.ts

## Question
The unsubscribe in linkWithCrossAppAuth runs only after a successful link; can an attacker make the link throw so the listener stays attached and keeps capturing later tokens through linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode?

## Target
- File/function: [src/action/crossApp/linkWithCrossAppAuth.ts](src/action/crossApp/linkWithCrossAppAuth.ts) - linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode, listener unsubscribed after
- Entrypoint: privy.crossApp.linkWithCrossAppAuth({providerAppId, redirectUrl})
- Attacker controls: providerAppId, redirectUrl, oauth_tokens emitted while the listener is attached
- Exploit idea: Force the link to reject and then trigger another OAuth flow.
- Invariant to test: Listeners must be removed on every exit path.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: force a rejection in linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode and assert the listener is removed.
