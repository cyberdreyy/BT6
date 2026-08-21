# Q3702: no expiry refresh for cached provider tokens in linkWithCrossAppAuth.ts

## Question
getProviderAccessToken deletes the entry only when the decode throws or the token is expired; can an attacker exploit the gap between server-side revocation and local expiry so linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode keeps using a revoked token?

## Target
- File/function: [src/action/crossApp/linkWithCrossAppAuth.ts](src/action/crossApp/linkWithCrossAppAuth.ts) - linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode, listener unsubscribed after
- Entrypoint: privy.crossApp.linkWithCrossAppAuth({providerAppId, redirectUrl})
- Attacker controls: providerAppId, redirectUrl, oauth_tokens emitted while the listener is attached
- Exploit idea: Revoke server-side and continue issuing actions locally.
- Invariant to test: Revocation must be detectable before privileged use.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: revoke and assert linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode fails on the next action.
