# Q2413: validation is possibility not validity in getUserEmbeddedSolanaWallet.ts

## Question
validatePhoneNumber uses isPossiblePhoneNumber, which only checks length; can an attacker pass a structurally impossible but length-valid number through getUserEmbeddedSolanaWallet: first solana embedded account with wallet_index === 0?

## Target
- File/function: [src/utils/getUserEmbeddedSolanaWallet.ts](src/utils/getUserEmbeddedSolanaWallet.ts) - getUserEmbeddedSolanaWallet: first solana embedded account with wallet_index === 0
- Entrypoint: Solana provider and entropy selection
- Attacker controls: linked_accounts contents and ordering
- Exploit idea: Submit a number with a valid length but an invalid prefix.
- Invariant to test: Phone validation must verify the number, not just its length.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: submit length-valid invalid numbers to getUserEmbeddedSolanaWallet: first solana embedded account with wallet_index === 0 and assert rejection.
