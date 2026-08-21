# Q3262: request json embedded unescaped in the url in linkWithCrossAppAuth.ts

## Question
The request object is JSON.stringified into a query parameter; can an attacker craft request content through linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode that alters the resulting URL structure?

## Target
- File/function: [src/action/crossApp/linkWithCrossAppAuth.ts](src/action/crossApp/linkWithCrossAppAuth.ts) - linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode, listener unsubscribed after
- Entrypoint: privy.crossApp.linkWithCrossAppAuth({providerAppId, redirectUrl})
- Attacker controls: providerAppId, redirectUrl, oauth_tokens emitted while the listener is attached
- Exploit idea: Include characters that affect URL parsing in the request content.
- Invariant to test: URL parameters must be encoded so content cannot alter structure.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: include URL metacharacters in linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode's request and assert encoding.
