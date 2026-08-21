# Q0952: storage key namespaced only by provider app id in linkWithCrossAppAuth.ts

## Question
The cache key is privy:cross-app:<providerAppId>; can an attacker use a providerAppId string through linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode that collides with another key namespace or with a different app's entry?

## Target
- File/function: [src/action/crossApp/linkWithCrossAppAuth.ts](src/action/crossApp/linkWithCrossAppAuth.ts) - linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode, listener unsubscribed after
- Entrypoint: privy.crossApp.linkWithCrossAppAuth({providerAppId, redirectUrl})
- Attacker controls: providerAppId, redirectUrl, oauth_tokens emitted while the listener is attached
- Exploit idea: Pass a providerAppId containing ':' or matching another key prefix.
- Invariant to test: Storage keys must be injective over provider app ids.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: pass separator-bearing provider ids to linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode and assert distinct keys.
