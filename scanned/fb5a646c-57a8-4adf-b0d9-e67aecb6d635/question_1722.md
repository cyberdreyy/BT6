# Q1722: wallet address resolves the provider app in linkWithCrossAppAuth.ts

## Question
getCrossAppAccountByWalletAddress picks the first cross_app account whose embedded_wallets or smart_wallets contains the address; can an attacker cause two accounts to contain the same address so linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode routes the request to the wrong provider app?

## Target
- File/function: [src/action/crossApp/linkWithCrossAppAuth.ts](src/action/crossApp/linkWithCrossAppAuth.ts) - linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode, listener unsubscribed after
- Entrypoint: privy.crossApp.linkWithCrossAppAuth({providerAppId, redirectUrl})
- Attacker controls: providerAppId, redirectUrl, oauth_tokens emitted while the listener is attached
- Exploit idea: Construct a user with duplicate addresses across cross_app accounts.
- Invariant to test: Address to provider resolution must be unique and verified.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: build duplicate-address accounts and assert linkWithCrossAppAuth: addOAuthTokensListener then oauth.linkWithCode refuses to guess.
