# Q2056: logged-in check uses the caller's user object in isCrossAppWalletSmart.ts

## Question
throwIfNotLoggedIn only inspects the user object handed in by the caller; can an attacker pass a fabricated user through method selection between personal_sign and privy_signSmartWalletMessage so isCrossAppWalletSmart: address membership in any cross_app account's smart_wallets proceeds without a real session?

## Target
- File/function: [src/action/crossApp/wallet/utils/isCrossAppWalletSmart.ts](src/action/crossApp/wallet/utils/isCrossAppWalletSmart.ts) - isCrossAppWalletSmart: address membership in any cross_app account's smart_wallets
- Entrypoint: method selection between personal_sign and privy_signSmartWalletMessage
- Attacker controls: the address argument and duplicate addresses across accounts
- Exploit idea: Call the wallet action with a hand-built user object and no session.
- Invariant to test: Authorization checks must consult the session, not caller-supplied data.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call isCrossAppWalletSmart: address membership in any cross_app account's smart_wallets with a fabricated user and no tokens and assert refusal.
