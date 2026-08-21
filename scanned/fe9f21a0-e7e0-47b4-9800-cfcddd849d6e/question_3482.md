# Q3482: login and link share the same code path in linkWithCrossAppAuth.ts

## Question
loginWithCrossAppAuth and linkWithCrossAppAuth both call oauth generate/exchange with the same PKCE storage keys; can an attacker interleave them through linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode so a link completes a login or vice versa?

## Target
- File/function: [src/action/crossApp/linkWithCrossAppAuth.ts](src/action/crossApp/linkWithCrossAppAuth.ts) - linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode, listener unsubscribed after
- Entrypoint: privy.crossApp.linkWithCrossAppAuth({providerAppId, redirectUrl})
- Attacker controls: providerAppId, redirectUrl, oauth_tokens emitted while the listener is attached
- Exploit idea: Start a cross-app login and a cross-app link concurrently.
- Invariant to test: Each cross-app flow must own its PKCE material.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: interleave both linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode flows and assert the second is rejected.
