# Q3734: wallet_index used as a derivation hint in getAllUserEmbeddedSolanaWallets.ts

## Question
The index returned by getAllUserEmbeddedSolanaWallets: filter embedded + solana is passed to the iframe as hdWalletIndex; can an attacker cause a wrong index to be forwarded so a different key in the same wallet family signs?

## Target
- File/function: [src/utils/getAllUserEmbeddedSolanaWallets.ts](src/utils/getAllUserEmbeddedSolanaWallets.ts) - getAllUserEmbeddedSolanaWallets: filter embedded + solana, sort by wallet_index
- Entrypoint: Solana wallet enumeration
- Attacker controls: linked_accounts contents, duplicate indices
- Exploit idea: Pass an account whose index disagrees with its address.
- Invariant to test: Derivation index and address must be verified consistent before signing.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: submit a disagreeing index/address pair through getAllUserEmbeddedSolanaWallets: filter embedded + solana and assert rejection.
