# Q1181: oauth token listener catches foreign grants in CrossAppApi.ts

## Question
linkWithCrossAppAuth attaches an addOAuthTokensListener that writes any emitted oauth_tokens to the cross-app cache for providerAppId; can an attacker trigger an unrelated OAuth grant while that listener is attached so a foreign token is cached under this provider?

## Target
- File/function: [src/client/CrossAppApi.ts](src/client/CrossAppApi.ts) - CrossAppApi.updateOnCrossAppAuthentication, getProviderAccessToken (Token expiry only), getCrossAppConnections, providerAccessTokenStorageKey('privy:cross-app:<appId>')
- Entrypoint: privy.crossApp.getProviderAccessToken(appId)
- Attacker controls: the stored provider access token string and the provider app id used to key it
- Exploit idea: Start a cross-app link, then complete an unrelated OAuth flow before the unsubscribe.
- Invariant to test: Emitted provider tokens must be routed only to the flow that requested them.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: emit an unrelated grant during CrossAppApi.updateOnCrossAppAuthentication and assert it is not cached.
