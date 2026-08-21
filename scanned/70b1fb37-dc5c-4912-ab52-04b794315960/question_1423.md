# Q1423: linked_accounts order is server supplied in getUserEmbeddedSolanaWallet.ts

## Question
getUserEmbeddedSolanaWallet: first solana embedded account with wallet_index === 0 depends on the order of user.linked_accounts as returned by the API; can an attacker influence that order so a different wallet becomes primary?

## Target
- File/function: [src/utils/getUserEmbeddedSolanaWallet.ts](src/utils/getUserEmbeddedSolanaWallet.ts) - getUserEmbeddedSolanaWallet: first solana embedded account with wallet_index === 0
- Entrypoint: Solana provider and entropy selection
- Attacker controls: linked_accounts contents and ordering
- Exploit idea: Return the same accounts in a different order and compare selections.
- Invariant to test: Selection must be order-independent.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: permute the account list and assert getUserEmbeddedSolanaWallet: first solana embedded account with wallet_index === 0 returns the same wallet.
