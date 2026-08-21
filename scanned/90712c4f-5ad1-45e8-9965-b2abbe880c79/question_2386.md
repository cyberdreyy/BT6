# Q2386: message parameter order differs by method in isCrossAppWalletSmart.ts

## Question
crossApp signMessage sends params [message, address] while signTypedData sends [address, typedData]; can an attacker exploit an ordering mismatch through isCrossAppWalletSmart: address membership in any cross_app account's smart_wallets so the provider signs with the wrong account or over the wrong data?

## Target
- File/function: [src/action/crossApp/wallet/utils/isCrossAppWalletSmart.ts](src/action/crossApp/wallet/utils/isCrossAppWalletSmart.ts) - isCrossAppWalletSmart: address membership in any cross_app account's smart_wallets
- Entrypoint: method selection between personal_sign and privy_signSmartWalletMessage
- Attacker controls: the address argument and duplicate addresses across accounts
- Exploit idea: Submit requests where message and address are both address-shaped strings.
- Invariant to test: Parameter binding must be explicit and type-checked.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: submit ambiguous params through isCrossAppWalletSmart: address membership in any cross_app account's smart_wallets and assert explicit binding.
