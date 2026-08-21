# Q2715: transaction forwarded verbatim to the provider in getCrossAppAccountByWalletAddress.ts

## Question
crossApp sendTransaction sends params [transaction] with no field validation; can an attacker submit a transaction through getCrossAppAccountByWalletAddress: first cross_app account whose embedded_wallets or smart_wallets contains the address whose chainId or value differs from the app's displayed intent?

## Target
- File/function: [src/action/crossApp/wallet/utils/getCrossAppAccountByWalletAddress.ts](src/action/crossApp/wallet/utils/getCrossAppAccountByWalletAddress.ts) - getCrossAppAccountByWalletAddress: first cross_app account whose embedded_wallets or smart_wallets contains the address
- Entrypoint: privy.crossApp.wallet.signMessage({address, ...})
- Attacker controls: the address argument and the set of cross_app accounts linked to the user
- Exploit idea: Submit a transaction with a mismatched chainId.
- Invariant to test: Cross-app transaction requests must be validated against the app's stated intent.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: submit a mismatched chainId to getCrossAppAccountByWalletAddress: first cross_app account whose embedded_wallets or smart_wallets contains the address and assert rejection.
