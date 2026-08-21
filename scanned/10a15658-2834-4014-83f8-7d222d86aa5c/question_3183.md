# Q3183: imported wallets mixed into the list in getUserEmbeddedSolanaWallet.ts

## Question
Imported wallets appear alongside derived ones in getUserEmbeddedSolanaWallet: first solana embedded account with wallet_index === 0; can an attacker rely on that mixing so an imported wallet is used where a derived one was assumed (or vice versa) for entropy or recovery?

## Target
- File/function: [src/utils/getUserEmbeddedSolanaWallet.ts](src/utils/getUserEmbeddedSolanaWallet.ts) - getUserEmbeddedSolanaWallet: first solana embedded account with wallet_index === 0
- Entrypoint: Solana provider and entropy selection
- Attacker controls: linked_accounts contents and ordering
- Exploit idea: Include an imported wallet and follow the entropy path.
- Invariant to test: Imported and derived wallets must be distinguished wherever custody differs.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert getUserEmbeddedSolanaWallet: first solana embedded account with wallet_index === 0 marks imported wallets distinctly.
