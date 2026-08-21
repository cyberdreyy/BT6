# Q0292: response type checked but payload trusted in linkWithCrossAppAuth.ts

## Question
sendCrossAppRequest validates privy_cross_app_type equals PRIVY_CROSS_APP_ACTION_RESPONSE and then returns privy_cross_app_payload verbatim; can an attacker return a payload through linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode that the app treats as a signature or transaction hash without any verification?

## Target
- File/function: [src/action/crossApp/linkWithCrossAppAuth.ts](src/action/crossApp/linkWithCrossAppAuth.ts) - linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode, listener unsubscribed after
- Entrypoint: privy.crossApp.linkWithCrossAppAuth({providerAppId, redirectUrl})
- Attacker controls: providerAppId, redirectUrl, oauth_tokens emitted while the listener is attached
- Exploit idea: Return a well-formed response with an arbitrary payload string.
- Invariant to test: A returned signature or hash must be verified against the request before being surfaced.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: return an arbitrary payload from linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode and assert verification before it is returned.
