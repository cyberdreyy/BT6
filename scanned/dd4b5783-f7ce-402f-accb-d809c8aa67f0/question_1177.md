# Q1177: oauth token listener catches foreign grants in throwIfNotLoggedIn.ts

## Question
linkWithCrossAppAuth attaches an addOAuthTokensListener that writes any emitted oauth_tokens to the cross-app cache for providerAppId; can an attacker trigger an unrelated OAuth grant while that listener is attached so a foreign token is cached under this provider?

## Target
- File/function: [src/action/crossApp/wallet/utils/throwIfNotLoggedIn.ts](src/action/crossApp/wallet/utils/throwIfNotLoggedIn.ts) - throwIfNotLoggedIn(user): only checks the user object passed by the caller
- Entrypoint: every crossApp.wallet action
- Attacker controls: the user object supplied by the caller rather than read from session
- Exploit idea: Start a cross-app link, then complete an unrelated OAuth flow before the unsubscribe.
- Invariant to test: Emitted provider tokens must be routed only to the flow that requested them.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: emit an unrelated grant during throwIfNotLoggedIn(user): only checks the user object passed by the caller and assert it is not cached.
