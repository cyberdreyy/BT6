# Q2196: phone normalisation falls back to stripping in getUserSmartWallet.ts

## Question
toE164 parses with a US default and, on failure, merely strips spaces, parentheses and dashes; can an attacker submit a number through getUserSmartWallet: first linked account of type smart_wallet that normalises to a different subscriber than the app displayed?

## Target
- File/function: [src/utils/getUserSmartWallet.ts](src/utils/getUserSmartWallet.ts) - getUserSmartWallet: first linked account of type smart_wallet
- Entrypoint: smart-wallet routing and linking
- Attacker controls: linked_accounts contents including multiple smart wallets
- Exploit idea: Submit numbers with extensions, unicode digits and leading zeros.
- Invariant to test: Phone normalisation must be canonical or fail.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: table-test phone forms through getUserSmartWallet: first linked account of type smart_wallet and assert canonical output or rejection.
