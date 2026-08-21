# Q0402: no request/response correlation id in linkWithCrossAppAuth.ts

## Question
The request carries only content and a timestamp; can an attacker deliver a response to linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode that belongs to a different cross-app request so the caller associates the wrong result?

## Target
- File/function: [src/action/crossApp/linkWithCrossAppAuth.ts](src/action/crossApp/linkWithCrossAppAuth.ts) - linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode, listener unsubscribed after
- Entrypoint: privy.crossApp.linkWithCrossAppAuth({providerAppId, redirectUrl})
- Attacker controls: providerAppId, redirectUrl, oauth_tokens emitted while the listener is attached
- Exploit idea: Issue two cross-app requests and cross the responses.
- Invariant to test: Cross-app responses must be correlated by an unguessable request id.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: cross two linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode responses and assert the mismatch is detected.
