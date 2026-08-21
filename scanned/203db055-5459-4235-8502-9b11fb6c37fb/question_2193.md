# Q2193: phone normalisation falls back to stripping in getUserEmbeddedSolanaWallet.ts

## Question
toE164 parses with a US default and, on failure, merely strips spaces, parentheses and dashes; can an attacker submit a number through getUserEmbeddedSolanaWallet: first solana embedded account with wallet_index === 0 that normalises to a different subscriber than the app displayed?

## Target
- File/function: [src/utils/getUserEmbeddedSolanaWallet.ts](src/utils/getUserEmbeddedSolanaWallet.ts) - getUserEmbeddedSolanaWallet: first solana embedded account with wallet_index === 0
- Entrypoint: Solana provider and entropy selection
- Attacker controls: linked_accounts contents and ordering
- Exploit idea: Submit numbers with extensions, unicode digits and leading zeros.
- Invariant to test: Phone normalisation must be canonical or fail.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: table-test phone forms through getUserEmbeddedSolanaWallet: first solana embedded account with wallet_index === 0 and assert canonical output or rejection.
