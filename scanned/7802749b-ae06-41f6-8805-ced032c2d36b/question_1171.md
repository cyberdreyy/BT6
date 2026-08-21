# Q1171: oauth token listener catches foreign grants in loginWithCrossAppAuth.ts

## Question
linkWithCrossAppAuth attaches an addOAuthTokensListener that writes any emitted oauth_tokens to the cross-app cache for providerAppId; can an attacker trigger an unrelated OAuth grant while that listener is attached so a foreign token is cached under this provider?

## Target
- File/function: [src/action/crossApp/loginWithCrossAppAuth.ts](src/action/crossApp/loginWithCrossAppAuth.ts) - loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}`, redirectUrl) -> openAuthSession -> oauth.loginWithCode -> crossApp.updateOnCrossAppAuthentication
- Entrypoint: privy.crossApp.loginWithCrossAppAuth({providerAppId, redirectUrl})
- Attacker controls: providerAppId string, redirectUrl, the privy_oauth_state / privy_oauth_code values returned by the auth session
- Exploit idea: Start a cross-app link, then complete an unrelated OAuth flow before the unsubscribe.
- Invariant to test: Emitted provider tokens must be routed only to the flow that requested them.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: emit an unrelated grant during loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}` and assert it is not cached.
