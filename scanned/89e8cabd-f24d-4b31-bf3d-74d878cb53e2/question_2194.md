# Q2194: phone normalisation falls back to stripping in getAllUserEmbeddedSolanaWallets.ts

## Question
toE164 parses with a US default and, on failure, merely strips spaces, parentheses and dashes; can an attacker submit a number through getAllUserEmbeddedSolanaWallets: filter embedded + solana that normalises to a different subscriber than the app displayed?

## Target
- File/function: [src/utils/getAllUserEmbeddedSolanaWallets.ts](src/utils/getAllUserEmbeddedSolanaWallets.ts) - getAllUserEmbeddedSolanaWallets: filter embedded + solana, sort by wallet_index
- Entrypoint: Solana wallet enumeration
- Attacker controls: linked_accounts contents, duplicate indices
- Exploit idea: Submit numbers with extensions, unicode digits and leading zeros.
- Invariant to test: Phone normalisation must be canonical or fail.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: table-test phone forms through getAllUserEmbeddedSolanaWallets: filter embedded + solana and assert canonical output or rejection.
