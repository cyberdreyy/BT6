# Q0324: null user returns an empty result in getAllUserEmbeddedSolanaWallets.ts

## Question
getAllUserEmbeddedSolanaWallets: filter embedded + solana returns null or [] for a null user; can an attacker exploit that silent empty result so a caller proceeds with an undefined wallet and signs or funds with the wrong account?

## Target
- File/function: [src/utils/getAllUserEmbeddedSolanaWallets.ts](src/utils/getAllUserEmbeddedSolanaWallets.ts) - getAllUserEmbeddedSolanaWallets: filter embedded + solana, sort by wallet_index
- Entrypoint: Solana wallet enumeration
- Attacker controls: linked_accounts contents, duplicate indices
- Exploit idea: Call the selection with a null user during a session gap.
- Invariant to test: Absence of a user must be an explicit error for wallet-selecting callers.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call getAllUserEmbeddedSolanaWallets: filter embedded + solana with null and assert callers cannot proceed.
