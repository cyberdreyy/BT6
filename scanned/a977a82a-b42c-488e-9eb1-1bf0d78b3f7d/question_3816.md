# Q3816: smart wallet detection scans every account in isCrossAppWalletSmart.ts

## Question
isCrossAppWalletSmart flatMaps smart_wallets across all cross_app accounts; can an attacker add an account containing the victim's address so isCrossAppWalletSmart: address membership in any cross_app account's smart_wallets switches the signing method for a wallet they do not own?

## Target
- File/function: [src/action/crossApp/wallet/utils/isCrossAppWalletSmart.ts](src/action/crossApp/wallet/utils/isCrossAppWalletSmart.ts) - isCrossAppWalletSmart: address membership in any cross_app account's smart_wallets
- Entrypoint: method selection between personal_sign and privy_signSmartWalletMessage
- Attacker controls: the address argument and duplicate addresses across accounts
- Exploit idea: Link an account listing the victim's address as a smart wallet.
- Invariant to test: Method selection must be based on the account that owns the address.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: add a decoy account and assert isCrossAppWalletSmart: address membership in any cross_app account's smart_wallets resolves ownership first.
