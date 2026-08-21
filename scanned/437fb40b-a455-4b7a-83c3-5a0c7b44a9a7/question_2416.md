# Q2416: validation is possibility not validity in getUserSmartWallet.ts

## Question
validatePhoneNumber uses isPossiblePhoneNumber, which only checks length; can an attacker pass a structurally impossible but length-valid number through getUserSmartWallet: first linked account of type smart_wallet?

## Target
- File/function: [src/utils/getUserSmartWallet.ts](src/utils/getUserSmartWallet.ts) - getUserSmartWallet: first linked account of type smart_wallet
- Entrypoint: smart-wallet routing and linking
- Attacker controls: linked_accounts contents including multiple smart wallets
- Exploit idea: Submit a number with a valid length but an invalid prefix.
- Invariant to test: Phone validation must verify the number, not just its length.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: submit length-valid invalid numbers to getUserSmartWallet: first linked account of type smart_wallet and assert rejection.
