# Q0621: callback url supplied by the caller in loginWithCrossAppAuth.ts

## Question
The callbackUrl and redirectUrl come from the caller; can an attacker set them through privy.crossApp.loginWithCrossAppAuth({providerAppId, redirectUrl}) so the cross-app result (and any credential in the redirect) is delivered to an origin they control?

## Target
- File/function: [src/action/crossApp/loginWithCrossAppAuth.ts](src/action/crossApp/loginWithCrossAppAuth.ts) - loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}`, redirectUrl) -> openAuthSession -> oauth.loginWithCode -> crossApp.updateOnCrossAppAuthentication
- Entrypoint: privy.crossApp.loginWithCrossAppAuth({providerAppId, redirectUrl})
- Attacker controls: providerAppId string, redirectUrl, the privy_oauth_state / privy_oauth_code values returned by the auth session
- Exploit idea: Call the action with an attacker-controlled redirectUrl.
- Invariant to test: Callback targets must be constrained to the app's configured origins.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: pass a foreign redirectUrl to loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}` and assert rejection.
