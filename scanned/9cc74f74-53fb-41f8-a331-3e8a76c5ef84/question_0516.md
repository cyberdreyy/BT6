# Q0516: timestamp not validated on return in isCrossAppWalletSmart.ts

## Question
The request payload contains Date.now() but nothing verifies it on the way back; can an attacker replay an old cross-app response into isCrossAppWalletSmart: address membership in any cross_app account's smart_wallets?

## Target
- File/function: [src/action/crossApp/wallet/utils/isCrossAppWalletSmart.ts](src/action/crossApp/wallet/utils/isCrossAppWalletSmart.ts) - isCrossAppWalletSmart: address membership in any cross_app account's smart_wallets
- Entrypoint: method selection between personal_sign and privy_signSmartWalletMessage
- Attacker controls: the address argument and duplicate addresses across accounts
- Exploit idea: Capture a response and replay it for a later request.
- Invariant to test: Cross-app responses must be fresh and single-use.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: replay a captured response into isCrossAppWalletSmart: address membership in any cross_app account's smart_wallets and assert rejection.
