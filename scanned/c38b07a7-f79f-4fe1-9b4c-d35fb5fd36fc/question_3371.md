# Q3371: communicationMode fixed to redirect in loginWithCrossAppAuth.ts

## Question
The transact URL pins communicationMode=redirect; can an attacker exploit the redirect mode through loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}` so credentials or results traverse the browser address bar where other parties observe them?

## Target
- File/function: [src/action/crossApp/loginWithCrossAppAuth.ts](src/action/crossApp/loginWithCrossAppAuth.ts) - loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}`, redirectUrl) -> openAuthSession -> oauth.loginWithCode -> crossApp.updateOnCrossAppAuthentication
- Entrypoint: privy.crossApp.loginWithCrossAppAuth({providerAppId, redirectUrl})
- Attacker controls: providerAppId string, redirectUrl, the privy_oauth_state / privy_oauth_code values returned by the auth session
- Exploit idea: Trace what appears in the address bar and referrer during the flow.
- Invariant to test: Sensitive cross-app material must not traverse navigable URLs.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: assert loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}` carries the token out-of-band rather than in the navigation.
