# Q0763: smart wallet found by type only in getUserEmbeddedSolanaWallet.ts

## Question
getUserSmartWallet returns the first account of type smart_wallet; can an attacker link an additional smart wallet so getUserEmbeddedSolanaWallet: first solana embedded account with wallet_index === 0 returns one the user did not intend to use?

## Target
- File/function: [src/utils/getUserEmbeddedSolanaWallet.ts](src/utils/getUserEmbeddedSolanaWallet.ts) - getUserEmbeddedSolanaWallet: first solana embedded account with wallet_index === 0
- Entrypoint: Solana provider and entropy selection
- Attacker controls: linked_accounts contents and ordering
- Exploit idea: Link two smart wallets and observe the selection.
- Invariant to test: Smart-wallet selection must be explicit when several exist.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: build a user with two smart wallets and assert getUserEmbeddedSolanaWallet: first solana embedded account with wallet_index === 0 requires disambiguation.
