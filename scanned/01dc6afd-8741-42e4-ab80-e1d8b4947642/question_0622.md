# Q0622: callback url supplied by the caller in linkWithCrossAppAuth.ts

## Question
The callbackUrl and redirectUrl come from the caller; can an attacker set them through privy.crossApp.linkWithCrossAppAuth({providerAppId, redirectUrl}) so the cross-app result (and any credential in the redirect) is delivered to an origin they control?

## Target
- File/function: [src/action/crossApp/linkWithCrossAppAuth.ts](src/action/crossApp/linkWithCrossAppAuth.ts) - linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode, listener unsubscribed after
- Entrypoint: privy.crossApp.linkWithCrossAppAuth({providerAppId, redirectUrl})
- Attacker controls: providerAppId, redirectUrl, oauth_tokens emitted while the listener is attached
- Exploit idea: Call the action with an attacker-controlled redirectUrl.
- Invariant to test: Callback targets must be constrained to the app's configured origins.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: pass a foreign redirectUrl to linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode and assert rejection.
