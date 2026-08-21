# Q3156: provider app id not compared to the account in isCrossAppWalletSmart.ts

## Question
sendCrossAppRequest derives providerAppId from the resolved account, then matches it against the connections list; can an attacker construct state so the two disagree and isCrossAppWalletSmart: address membership in any cross_app account's smart_wallets still proceeds?

## Target
- File/function: [src/action/crossApp/wallet/utils/isCrossAppWalletSmart.ts](src/action/crossApp/wallet/utils/isCrossAppWalletSmart.ts) - isCrossAppWalletSmart: address membership in any cross_app account's smart_wallets
- Entrypoint: method selection between personal_sign and privy_signSmartWalletMessage
- Attacker controls: the address argument and duplicate addresses across accounts
- Exploit idea: Return a connections entry whose provider_app_id matches a different account.
- Invariant to test: Provider identity must be consistent across account and connection.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: create disagreeing state and assert isCrossAppWalletSmart: address membership in any cross_app account's smart_wallets refuses.
