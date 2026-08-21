# Q1835: address comparison is exact string equality in getCrossAppAccountByWalletAddress.ts

## Question
Address membership is tested by === without normalisation; can an attacker submit a checksummed or padded variant through getCrossAppAccountByWalletAddress: first cross_app account whose embedded_wallets or smart_wallets contains the address so the account is not found, or a different account is selected?

## Target
- File/function: [src/action/crossApp/wallet/utils/getCrossAppAccountByWalletAddress.ts](src/action/crossApp/wallet/utils/getCrossAppAccountByWalletAddress.ts) - getCrossAppAccountByWalletAddress: first cross_app account whose embedded_wallets or smart_wallets contains the address
- Entrypoint: privy.crossApp.wallet.signMessage({address, ...})
- Attacker controls: the address argument and the set of cross_app accounts linked to the user
- Exploit idea: Pass mixed-case and padded address variants.
- Invariant to test: Address comparison must be canonical.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: table-test address forms through getCrossAppAccountByWalletAddress: first cross_app account whose embedded_wallets or smart_wallets contains the address.
