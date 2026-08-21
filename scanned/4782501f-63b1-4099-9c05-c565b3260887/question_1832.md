# Q1832: address comparison is exact string equality in linkWithCrossAppAuth.ts

## Question
Address membership is tested by === without normalisation; can an attacker submit a checksummed or padded variant through linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode so the account is not found, or a different account is selected?

## Target
- File/function: [src/action/crossApp/linkWithCrossAppAuth.ts](src/action/crossApp/linkWithCrossAppAuth.ts) - linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode, listener unsubscribed after
- Entrypoint: privy.crossApp.linkWithCrossAppAuth({providerAppId, redirectUrl})
- Attacker controls: providerAppId, redirectUrl, oauth_tokens emitted while the listener is attached
- Exploit idea: Pass mixed-case and padded address variants.
- Invariant to test: Address comparison must be canonical.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: table-test address forms through linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode.
