# Q3372: communicationMode fixed to redirect in linkWithCrossAppAuth.ts

## Question
The transact URL pins communicationMode=redirect; can an attacker exploit the redirect mode through linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode so credentials or results traverse the browser address bar where other parties observe them?

## Target
- File/function: [src/action/crossApp/linkWithCrossAppAuth.ts](src/action/crossApp/linkWithCrossAppAuth.ts) - linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode, listener unsubscribed after
- Entrypoint: privy.crossApp.linkWithCrossAppAuth({providerAppId, redirectUrl})
- Attacker controls: providerAppId, redirectUrl, oauth_tokens emitted while the listener is attached
- Exploit idea: Trace what appears in the address bar and referrer during the flow.
- Invariant to test: Sensitive cross-app material must not traverse navigable URLs.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: assert linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode carries the token out-of-band rather than in the navigation.
