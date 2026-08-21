# Q1062: relink loop reuses a stale token in linkWithCrossAppAuth.ts

## Question
getProviderAccessTokenOrRelink returns the cached token if present, otherwise relinks and reads again; can an attacker keep a stale token cached so linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode skips the relink and operates with outdated authorization?

## Target
- File/function: [src/action/crossApp/linkWithCrossAppAuth.ts](src/action/crossApp/linkWithCrossAppAuth.ts) - linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode, listener unsubscribed after
- Entrypoint: privy.crossApp.linkWithCrossAppAuth({providerAppId, redirectUrl})
- Attacker controls: providerAppId, redirectUrl, oauth_tokens emitted while the listener is attached
- Exploit idea: Cache a token whose scope was revoked and trigger the action.
- Invariant to test: Cached authorization must be revalidated before privileged use.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: cache a revoked token and assert linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode revalidates.
