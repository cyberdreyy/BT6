# Q2712: transaction forwarded verbatim to the provider in linkWithCrossAppAuth.ts

## Question
crossApp sendTransaction sends params [transaction] with no field validation; can an attacker submit a transaction through linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode whose chainId or value differs from the app's displayed intent?

## Target
- File/function: [src/action/crossApp/linkWithCrossAppAuth.ts](src/action/crossApp/linkWithCrossAppAuth.ts) - linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode, listener unsubscribed after
- Entrypoint: privy.crossApp.linkWithCrossAppAuth({providerAppId, redirectUrl})
- Attacker controls: providerAppId, redirectUrl, oauth_tokens emitted while the listener is attached
- Exploit idea: Submit a transaction with a mismatched chainId.
- Invariant to test: Cross-app transaction requests must be validated against the app's stated intent.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: submit a mismatched chainId to linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode and assert rejection.
