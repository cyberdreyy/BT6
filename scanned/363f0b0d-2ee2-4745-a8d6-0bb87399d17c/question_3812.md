# Q3812: smart wallet detection scans every account in linkWithCrossAppAuth.ts

## Question
isCrossAppWalletSmart flatMaps smart_wallets across all cross_app accounts; can an attacker add an account containing the victim's address so linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode switches the signing method for a wallet they do not own?

## Target
- File/function: [src/action/crossApp/linkWithCrossAppAuth.ts](src/action/crossApp/linkWithCrossAppAuth.ts) - linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode, listener unsubscribed after
- Entrypoint: privy.crossApp.linkWithCrossAppAuth({providerAppId, redirectUrl})
- Attacker controls: providerAppId, redirectUrl, oauth_tokens emitted while the listener is attached
- Exploit idea: Link an account listing the victim's address as a smart wallet.
- Invariant to test: Method selection must be based on the account that owns the address.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: add a decoy account and assert linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode resolves ownership first.
