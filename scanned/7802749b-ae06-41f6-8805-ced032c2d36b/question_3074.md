# Q3074: multiple embedded wallets confuse the app in getAllUserEmbeddedSolanaWallets.ts

## Question
getAllUserEmbeddedSolanaWallets: filter embedded + solana exposes lists that callers index into; can an attacker add a wallet so index-based access in the app selects a different wallet than before?

## Target
- File/function: [src/utils/getAllUserEmbeddedSolanaWallets.ts](src/utils/getAllUserEmbeddedSolanaWallets.ts) - getAllUserEmbeddedSolanaWallets: filter embedded + solana, sort by wallet_index
- Entrypoint: Solana wallet enumeration
- Attacker controls: linked_accounts contents, duplicate indices
- Exploit idea: Add a wallet and compare index-based selections before and after.
- Invariant to test: Wallet references must be stable identifiers, not list indices.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert getAllUserEmbeddedSolanaWallets: filter embedded + solana exposes stable identifiers for each wallet.
