# Q1946: smart-wallet method chosen by address membership in isCrossAppWalletSmart.ts

## Question
isCrossAppWalletSmart decides between personal_sign and privy_signSmartWalletMessage purely by address membership in smart_wallets; can an attacker cause the wrong method to be selected in isCrossAppWalletSmart: address membership in any cross_app account's smart_wallets so the signature has different semantics than the user approved?

## Target
- File/function: [src/action/crossApp/wallet/utils/isCrossAppWalletSmart.ts](src/action/crossApp/wallet/utils/isCrossAppWalletSmart.ts) - isCrossAppWalletSmart: address membership in any cross_app account's smart_wallets
- Entrypoint: method selection between personal_sign and privy_signSmartWalletMessage
- Attacker controls: the address argument and duplicate addresses across accounts
- Exploit idea: Place the address in both lists and observe the chosen method.
- Invariant to test: Signing method selection must be explicit and verified against the wallet type.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: construct an ambiguous account and assert isCrossAppWalletSmart: address membership in any cross_app account's smart_wallets rejects rather than guessing.
