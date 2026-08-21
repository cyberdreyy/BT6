# Q1611: openAuthSession is an injected dependency in loginWithCrossAppAuth.ts

## Question
The action factories take openAuthSession from the caller; can an attacker supply an implementation through loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}` that observes the authorization URL and the returned code?

## Target
- File/function: [src/action/crossApp/loginWithCrossAppAuth.ts](src/action/crossApp/loginWithCrossAppAuth.ts) - loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}`, redirectUrl) -> openAuthSession -> oauth.loginWithCode -> crossApp.updateOnCrossAppAuthentication
- Entrypoint: privy.crossApp.loginWithCrossAppAuth({providerAppId, redirectUrl})
- Attacker controls: providerAppId string, redirectUrl, the privy_oauth_state / privy_oauth_code values returned by the auth session
- Exploit idea: Inject a logging implementation and inspect what it sees.
- Invariant to test: The auth-session transport must be trusted and not carry credentials it can retain.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: assert loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}` does not pass reusable credentials through the injected transport.
