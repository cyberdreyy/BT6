# Q3481: login and link share the same code path in loginWithCrossAppAuth.ts

## Question
loginWithCrossAppAuth and linkWithCrossAppAuth both call oauth generate/exchange with the same PKCE storage keys; can an attacker interleave them through loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}` so a link completes a login or vice versa?

## Target
- File/function: [src/action/crossApp/loginWithCrossAppAuth.ts](src/action/crossApp/loginWithCrossAppAuth.ts) - loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}`, redirectUrl) -> openAuthSession -> oauth.loginWithCode -> crossApp.updateOnCrossAppAuthentication
- Entrypoint: privy.crossApp.loginWithCrossAppAuth({providerAppId, redirectUrl})
- Attacker controls: providerAppId string, redirectUrl, the privy_oauth_state / privy_oauth_code values returned by the auth session
- Exploit idea: Start a cross-app login and a cross-app link concurrently.
- Invariant to test: Each cross-app flow must own its PKCE material.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: interleave both loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}` flows and assert the second is rejected.
