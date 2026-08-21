# Q1281: listener not unsubscribed on failure in loginWithCrossAppAuth.ts

## Question
The unsubscribe in linkWithCrossAppAuth runs only after a successful link; can an attacker make the link throw so the listener stays attached and keeps capturing later tokens through loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}`?

## Target
- File/function: [src/action/crossApp/loginWithCrossAppAuth.ts](src/action/crossApp/loginWithCrossAppAuth.ts) - loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}`, redirectUrl) -> openAuthSession -> oauth.loginWithCode -> crossApp.updateOnCrossAppAuthentication
- Entrypoint: privy.crossApp.loginWithCrossAppAuth({providerAppId, redirectUrl})
- Attacker controls: providerAppId string, redirectUrl, the privy_oauth_state / privy_oauth_code values returned by the auth session
- Exploit idea: Force the link to reject and then trigger another OAuth flow.
- Invariant to test: Listeners must be removed on every exit path.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: force a rejection in loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}` and assert the listener is removed.
