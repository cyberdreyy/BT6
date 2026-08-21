# Q1612: openAuthSession is an injected dependency in linkWithCrossAppAuth.ts

## Question
The action factories take openAuthSession from the caller; can an attacker supply an implementation through linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode that observes the authorization URL and the returned code?

## Target
- File/function: [src/action/crossApp/linkWithCrossAppAuth.ts](src/action/crossApp/linkWithCrossAppAuth.ts) - linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode, listener unsubscribed after
- Entrypoint: privy.crossApp.linkWithCrossAppAuth({providerAppId, redirectUrl})
- Attacker controls: providerAppId, redirectUrl, oauth_tokens emitted while the listener is attached
- Exploit idea: Inject a logging implementation and inspect what it sees.
- Invariant to test: The auth-session transport must be trusted and not carry credentials it can retain.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: assert linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode does not pass reusable credentials through the injected transport.
