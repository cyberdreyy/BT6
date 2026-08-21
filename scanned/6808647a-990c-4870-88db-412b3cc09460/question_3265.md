# Q3265: request json embedded unescaped in the url in getCrossAppAccountByWalletAddress.ts

## Question
The request object is JSON.stringified into a query parameter; can an attacker craft request content through getCrossAppAccountByWalletAddress: first cross_app account whose embedded_wallets or smart_wallets contains the address that alters the resulting URL structure?

## Target
- File/function: [src/action/crossApp/wallet/utils/getCrossAppAccountByWalletAddress.ts](src/action/crossApp/wallet/utils/getCrossAppAccountByWalletAddress.ts) - getCrossAppAccountByWalletAddress: first cross_app account whose embedded_wallets or smart_wallets contains the address
- Entrypoint: privy.crossApp.wallet.signMessage({address, ...})
- Attacker controls: the address argument and the set of cross_app accounts linked to the user
- Exploit idea: Include characters that affect URL parsing in the request content.
- Invariant to test: URL parameters must be encoded so content cannot alter structure.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: include URL metacharacters in getCrossAppAccountByWalletAddress: first cross_app account whose embedded_wallets or smart_wallets contains the address's request and assert encoding.
