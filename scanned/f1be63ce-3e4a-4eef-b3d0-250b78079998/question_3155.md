# Q3155: provider app id not compared to the account in getCrossAppAccountByWalletAddress.ts

## Question
sendCrossAppRequest derives providerAppId from the resolved account, then matches it against the connections list; can an attacker construct state so the two disagree and getCrossAppAccountByWalletAddress: first cross_app account whose embedded_wallets or smart_wallets contains the address still proceeds?

## Target
- File/function: [src/action/crossApp/wallet/utils/getCrossAppAccountByWalletAddress.ts](src/action/crossApp/wallet/utils/getCrossAppAccountByWalletAddress.ts) - getCrossAppAccountByWalletAddress: first cross_app account whose embedded_wallets or smart_wallets contains the address
- Entrypoint: privy.crossApp.wallet.signMessage({address, ...})
- Attacker controls: the address argument and the set of cross_app accounts linked to the user
- Exploit idea: Return a connections entry whose provider_app_id matches a different account.
- Invariant to test: Provider identity must be consistent across account and connection.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: create disagreeing state and assert getCrossAppAccountByWalletAddress: first cross_app account whose embedded_wallets or smart_wallets contains the address refuses.
