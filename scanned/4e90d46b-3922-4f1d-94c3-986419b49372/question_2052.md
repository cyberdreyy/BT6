# Q2052: logged-in check uses the caller's user object in linkWithCrossAppAuth.ts

## Question
throwIfNotLoggedIn only inspects the user object handed in by the caller; can an attacker pass a fabricated user through privy.crossApp.linkWithCrossAppAuth({providerAppId, redirectUrl}) so linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode proceeds without a real session?

## Target
- File/function: [src/action/crossApp/linkWithCrossAppAuth.ts](src/action/crossApp/linkWithCrossAppAuth.ts) - linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode, listener unsubscribed after
- Entrypoint: privy.crossApp.linkWithCrossAppAuth({providerAppId, redirectUrl})
- Attacker controls: providerAppId, redirectUrl, oauth_tokens emitted while the listener is attached
- Exploit idea: Call the wallet action with a hand-built user object and no session.
- Invariant to test: Authorization checks must consult the session, not caller-supplied data.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode with a fabricated user and no tokens and assert refusal.
