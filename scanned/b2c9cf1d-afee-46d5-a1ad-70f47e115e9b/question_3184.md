# Q3184: imported wallets mixed into the list in getAllUserEmbeddedSolanaWallets.ts

## Question
Imported wallets appear alongside derived ones in getAllUserEmbeddedSolanaWallets: filter embedded + solana; can an attacker rely on that mixing so an imported wallet is used where a derived one was assumed (or vice versa) for entropy or recovery?

## Target
- File/function: [src/utils/getAllUserEmbeddedSolanaWallets.ts](src/utils/getAllUserEmbeddedSolanaWallets.ts) - getAllUserEmbeddedSolanaWallets: filter embedded + solana, sort by wallet_index
- Entrypoint: Solana wallet enumeration
- Attacker controls: linked_accounts contents, duplicate indices
- Exploit idea: Include an imported wallet and follow the entropy path.
- Invariant to test: Imported and derived wallets must be distinguished wherever custody differs.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert getAllUserEmbeddedSolanaWallets: filter embedded + solana marks imported wallets distinctly.
