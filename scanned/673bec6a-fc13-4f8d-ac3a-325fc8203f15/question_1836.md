# Q1836: address comparison is exact string equality in isCrossAppWalletSmart.ts

## Question
Address membership is tested by === without normalisation; can an attacker submit a checksummed or padded variant through isCrossAppWalletSmart: address membership in any cross_app account's smart_wallets so the account is not found, or a different account is selected?

## Target
- File/function: [src/action/crossApp/wallet/utils/isCrossAppWalletSmart.ts](src/action/crossApp/wallet/utils/isCrossAppWalletSmart.ts) - isCrossAppWalletSmart: address membership in any cross_app account's smart_wallets
- Entrypoint: method selection between personal_sign and privy_signSmartWalletMessage
- Attacker controls: the address argument and duplicate addresses across accounts
- Exploit idea: Pass mixed-case and padded address variants.
- Invariant to test: Address comparison must be canonical.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: table-test address forms through isCrossAppWalletSmart: address membership in any cross_app account's smart_wallets.
