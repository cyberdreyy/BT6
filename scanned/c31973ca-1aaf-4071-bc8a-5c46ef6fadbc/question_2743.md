# Q2743: array helpers build objects from strings in getUserEmbeddedSolanaWallet.ts

## Question
toObjectKeys reduces an array of strings into an object with a constant value; can an attacker supply an entry such as __proto__ through getUserEmbeddedSolanaWallet: first solana embedded account with wallet_index === 0 so the produced object has a polluted prototype?

## Target
- File/function: [src/utils/getUserEmbeddedSolanaWallet.ts](src/utils/getUserEmbeddedSolanaWallet.ts) - getUserEmbeddedSolanaWallet: first solana embedded account with wallet_index === 0
- Entrypoint: Solana provider and entropy selection
- Attacker controls: linked_accounts contents and ordering
- Exploit idea: Pass prototype-named entries.
- Invariant to test: Object construction from input arrays must be prototype-safe.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: pass '__proto__' to getUserEmbeddedSolanaWallet: first solana embedded account with wallet_index === 0 and assert a null-prototype or filtered result.
