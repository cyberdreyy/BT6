# Q0951: storage key namespaced only by provider app id in loginWithCrossAppAuth.ts

## Question
The cache key is privy:cross-app:<providerAppId>; can an attacker use a providerAppId string through loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}` that collides with another key namespace or with a different app's entry?

## Target
- File/function: [src/action/crossApp/loginWithCrossAppAuth.ts](src/action/crossApp/loginWithCrossAppAuth.ts) - loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}`, redirectUrl) -> openAuthSession -> oauth.loginWithCode -> crossApp.updateOnCrossAppAuthentication
- Entrypoint: privy.crossApp.loginWithCrossAppAuth({providerAppId, redirectUrl})
- Attacker controls: providerAppId string, redirectUrl, the privy_oauth_state / privy_oauth_code values returned by the auth session
- Exploit idea: Pass a providerAppId containing ':' or matching another key prefix.
- Invariant to test: Storage keys must be injective over provider app ids.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: pass separator-bearing provider ids to loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}` and assert distinct keys.
