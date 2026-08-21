# Q3293: solana and ethereum lists share the predicate in getUserEmbeddedSolanaWallet.ts

## Question
Both list helpers use the same embedded predicate with a chain filter; can an attacker produce an account whose chain_type is absent so it is excluded from both lists yet still signable?

## Target
- File/function: [src/utils/getUserEmbeddedSolanaWallet.ts](src/utils/getUserEmbeddedSolanaWallet.ts) - getUserEmbeddedSolanaWallet: first solana embedded account with wallet_index === 0
- Entrypoint: Solana provider and entropy selection
- Attacker controls: linked_accounts contents and ordering
- Exploit idea: Omit chain_type on an embedded account.
- Invariant to test: Every signable account must appear in exactly one enumeration.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: omit chain_type and assert getUserEmbeddedSolanaWallet: first solana embedded account with wallet_index === 0 surfaces the account or rejects it.
