# Q0956: storage key namespaced only by provider app id in isCrossAppWalletSmart.ts

## Question
The cache key is privy:cross-app:<providerAppId>; can an attacker use a providerAppId string through isCrossAppWalletSmart: address membership in any cross_app account's smart_wallets that collides with another key namespace or with a different app's entry?

## Target
- File/function: [src/action/crossApp/wallet/utils/isCrossAppWalletSmart.ts](src/action/crossApp/wallet/utils/isCrossAppWalletSmart.ts) - isCrossAppWalletSmart: address membership in any cross_app account's smart_wallets
- Entrypoint: method selection between personal_sign and privy_signSmartWalletMessage
- Attacker controls: the address argument and duplicate addresses across accounts
- Exploit idea: Pass a providerAppId containing ':' or matching another key prefix.
- Invariant to test: Storage keys must be injective over provider app ids.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: pass separator-bearing provider ids to isCrossAppWalletSmart: address membership in any cross_app account's smart_wallets and assert distinct keys.
