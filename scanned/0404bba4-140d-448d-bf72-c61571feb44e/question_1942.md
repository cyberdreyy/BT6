# Q1942: smart-wallet method chosen by address membership in linkWithCrossAppAuth.ts

## Question
isCrossAppWalletSmart decides between personal_sign and privy_signSmartWalletMessage purely by address membership in smart_wallets; can an attacker cause the wrong method to be selected in linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode so the signature has different semantics than the user approved?

## Target
- File/function: [src/action/crossApp/linkWithCrossAppAuth.ts](src/action/crossApp/linkWithCrossAppAuth.ts) - linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode, listener unsubscribed after
- Entrypoint: privy.crossApp.linkWithCrossAppAuth({providerAppId, redirectUrl})
- Attacker controls: providerAppId, redirectUrl, oauth_tokens emitted while the listener is attached
- Exploit idea: Place the address in both lists and observe the chosen method.
- Invariant to test: Signing method selection must be explicit and verified against the wallet type.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: construct an ambiguous account and assert linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode rejects rather than guessing.
