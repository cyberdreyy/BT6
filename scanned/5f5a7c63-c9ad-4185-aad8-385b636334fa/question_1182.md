# Q1182: oauth token listener catches foreign grants in index.ts

## Question
linkWithCrossAppAuth attaches an addOAuthTokensListener that writes any emitted oauth_tokens to the cross-app cache for providerAppId; can an attacker trigger an unrelated OAuth grant while that listener is attached so a foreign token is cached under this provider?

## Target
- File/function: [src/action/crossApp/index.ts](src/action/crossApp/index.ts) - crossApp action barrel: loginWithCrossAppAuth, linkWithCrossAppAuth, wallet.{signMessage,signTypedData,sendTransaction}
- Entrypoint: privy.crossApp.*
- Attacker controls: which dependency object (client, openAuthSession) is bound to each action
- Exploit idea: Start a cross-app link, then complete an unrelated OAuth flow before the unsubscribe.
- Invariant to test: Emitted provider tokens must be routed only to the flow that requested them.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: emit an unrelated grant during crossApp action barrel: loginWithCrossAppAuth and assert it is not cached.
