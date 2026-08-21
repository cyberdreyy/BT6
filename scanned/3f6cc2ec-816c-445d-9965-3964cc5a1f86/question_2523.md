# Q2523: masked display reveals the last four in getUserEmbeddedSolanaWallet.ts

## Question
lastFourDigits renders '*' plus the final four digits; can an attacker use a surface fed by getUserEmbeddedSolanaWallet: first solana embedded account with wallet_index === 0 to confirm a victim's phone number by comparing masks?

## Target
- File/function: [src/utils/getUserEmbeddedSolanaWallet.ts](src/utils/getUserEmbeddedSolanaWallet.ts) - getUserEmbeddedSolanaWallet: first solana embedded account with wallet_index === 0
- Entrypoint: Solana provider and entropy selection
- Attacker controls: linked_accounts contents and ordering
- Exploit idea: Compare masks across candidate numbers.
- Invariant to test: Masked identifiers must not confirm guesses.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: assert getUserEmbeddedSolanaWallet: first solana embedded account with wallet_index === 0's mask does not distinguish candidate numbers by four digits alone.
