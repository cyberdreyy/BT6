# Q2744: array helpers build objects from strings in getAllUserEmbeddedSolanaWallets.ts

## Question
toObjectKeys reduces an array of strings into an object with a constant value; can an attacker supply an entry such as __proto__ through getAllUserEmbeddedSolanaWallets: filter embedded + solana so the produced object has a polluted prototype?

## Target
- File/function: [src/utils/getAllUserEmbeddedSolanaWallets.ts](src/utils/getAllUserEmbeddedSolanaWallets.ts) - getAllUserEmbeddedSolanaWallets: filter embedded + solana, sort by wallet_index
- Entrypoint: Solana wallet enumeration
- Attacker controls: linked_accounts contents, duplicate indices
- Exploit idea: Pass prototype-named entries.
- Invariant to test: Object construction from input arrays must be prototype-safe.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: pass '__proto__' to getAllUserEmbeddedSolanaWallets: filter embedded + solana and assert a null-prototype or filtered result.
