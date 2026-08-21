# Q2197: phone normalisation falls back to stripping in shouldCreateEmbeddedEthWallet.ts

## Question
toE164 parses with a US default and, on failure, merely strips spaces, parentheses and dashes; can an attacker submit a number through shouldCreateEmbeddedEthWallet(user that normalises to a different subscriber than the app displayed?

## Target
- File/function: [src/utils/shouldCreateEmbeddedEthWallet.ts](src/utils/shouldCreateEmbeddedEthWallet.ts) - shouldCreateEmbeddedEthWallet(user, createOnLogin: 'off'|'users-without-wallets'|'all-users')
- Entrypoint: maybeCreateWalletOnLogin after every login
- Attacker controls: external wallets linked to the account and the createOnLogin setting
- Exploit idea: Submit numbers with extensions, unicode digits and leading zeros.
- Invariant to test: Phone normalisation must be canonical or fail.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: table-test phone forms through shouldCreateEmbeddedEthWallet(user and assert canonical output or rejection.
