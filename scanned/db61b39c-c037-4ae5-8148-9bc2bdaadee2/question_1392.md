# Q1392: provider app id becomes an oauth provider string in linkWithCrossAppAuth.ts

## Question
Cross-app auth calls oauth.generateURL with `privy:${providerAppId}`; can an attacker pass a providerAppId that produces a provider string the OAuth layer interprets differently through linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode?

## Target
- File/function: [src/action/crossApp/linkWithCrossAppAuth.ts](src/action/crossApp/linkWithCrossAppAuth.ts) - linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode, listener unsubscribed after
- Entrypoint: privy.crossApp.linkWithCrossAppAuth({providerAppId, redirectUrl})
- Attacker controls: providerAppId, redirectUrl, oauth_tokens emitted while the listener is attached
- Exploit idea: Pass ids containing ':' or known provider names.
- Invariant to test: Provider identifiers must be validated before being embedded in a provider string.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: pass crafted provider ids to linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode and assert validation.
