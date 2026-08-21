# Q3815: smart wallet detection scans every account in getCrossAppAccountByWalletAddress.ts

## Question
isCrossAppWalletSmart flatMaps smart_wallets across all cross_app accounts; can an attacker add an account containing the victim's address so getCrossAppAccountByWalletAddress: first cross_app account whose embedded_wallets or smart_wallets contains the address switches the signing method for a wallet they do not own?

## Target
- File/function: [src/action/crossApp/wallet/utils/getCrossAppAccountByWalletAddress.ts](src/action/crossApp/wallet/utils/getCrossAppAccountByWalletAddress.ts) - getCrossAppAccountByWalletAddress: first cross_app account whose embedded_wallets or smart_wallets contains the address
- Entrypoint: privy.crossApp.wallet.signMessage({address, ...})
- Attacker controls: the address argument and the set of cross_app accounts linked to the user
- Exploit idea: Link an account listing the victim's address as a smart wallet.
- Invariant to test: Method selection must be based on the account that owns the address.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: add a decoy account and assert getCrossAppAccountByWalletAddress: first cross_app account whose embedded_wallets or smart_wallets contains the address resolves ownership first.
