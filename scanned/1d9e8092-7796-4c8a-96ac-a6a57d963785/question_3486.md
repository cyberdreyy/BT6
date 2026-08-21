# Q3486: login and link share the same code path in isCrossAppWalletSmart.ts

## Question
loginWithCrossAppAuth and linkWithCrossAppAuth both call oauth generate/exchange with the same PKCE storage keys; can an attacker interleave them through isCrossAppWalletSmart: address membership in any cross_app account's smart_wallets so a link completes a login or vice versa?

## Target
- File/function: [src/action/crossApp/wallet/utils/isCrossAppWalletSmart.ts](src/action/crossApp/wallet/utils/isCrossAppWalletSmart.ts) - isCrossAppWalletSmart: address membership in any cross_app account's smart_wallets
- Entrypoint: method selection between personal_sign and privy_signSmartWalletMessage
- Attacker controls: the address argument and duplicate addresses across accounts
- Exploit idea: Start a cross-app login and a cross-app link concurrently.
- Invariant to test: Each cross-app flow must own its PKCE material.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: interleave both isCrossAppWalletSmart: address membership in any cross_app account's smart_wallets flows and assert the second is rejected.
