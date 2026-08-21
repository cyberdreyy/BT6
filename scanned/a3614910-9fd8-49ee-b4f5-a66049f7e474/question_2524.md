# Q2524: masked display reveals the last four in getAllUserEmbeddedSolanaWallets.ts

## Question
lastFourDigits renders '*' plus the final four digits; can an attacker use a surface fed by getAllUserEmbeddedSolanaWallets: filter embedded + solana to confirm a victim's phone number by comparing masks?

## Target
- File/function: [src/utils/getAllUserEmbeddedSolanaWallets.ts](src/utils/getAllUserEmbeddedSolanaWallets.ts) - getAllUserEmbeddedSolanaWallets: filter embedded + solana, sort by wallet_index
- Entrypoint: Solana wallet enumeration
- Attacker controls: linked_accounts contents, duplicate indices
- Exploit idea: Compare masks across candidate numbers.
- Invariant to test: Masked identifiers must not confirm guesses.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: assert getAllUserEmbeddedSolanaWallets: filter embedded + solana's mask does not distinguish candidate numbers by four digits alone.
