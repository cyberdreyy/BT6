# Q0296: response type checked but payload trusted in isCrossAppWalletSmart.ts

## Question
sendCrossAppRequest validates privy_cross_app_type equals PRIVY_CROSS_APP_ACTION_RESPONSE and then returns privy_cross_app_payload verbatim; can an attacker return a payload through isCrossAppWalletSmart: address membership in any cross_app account's smart_wallets that the app treats as a signature or transaction hash without any verification?

## Target
- File/function: [src/action/crossApp/wallet/utils/isCrossAppWalletSmart.ts](src/action/crossApp/wallet/utils/isCrossAppWalletSmart.ts) - isCrossAppWalletSmart: address membership in any cross_app account's smart_wallets
- Entrypoint: method selection between personal_sign and privy_signSmartWalletMessage
- Attacker controls: the address argument and duplicate addresses across accounts
- Exploit idea: Return a well-formed response with an arbitrary payload string.
- Invariant to test: A returned signature or hash must be verified against the request before being surfaced.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: return an arbitrary payload from isCrossAppWalletSmart: address membership in any cross_app account's smart_wallets and assert verification before it is returned.
