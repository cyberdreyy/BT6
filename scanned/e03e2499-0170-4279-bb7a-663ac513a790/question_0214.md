# Q0214: sort is not stable across equal indices in getAllUserEmbeddedSolanaWallets.ts

## Question
getAllUserEmbeddedSolanaWallets: filter embedded + solana sorts by wallet_index with a numeric comparator; can an attacker create equal indices so the resulting order (and therefore the selected wallet) varies between runs or engines?

## Target
- File/function: [src/utils/getAllUserEmbeddedSolanaWallets.ts](src/utils/getAllUserEmbeddedSolanaWallets.ts) - getAllUserEmbeddedSolanaWallets: filter embedded + solana, sort by wallet_index
- Entrypoint: Solana wallet enumeration
- Attacker controls: linked_accounts contents, duplicate indices
- Exploit idea: Create two accounts with identical wallet_index and compare orderings.
- Invariant to test: Selection must be deterministic for any account set.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert getAllUserEmbeddedSolanaWallets: filter embedded + solana is deterministic for equal-index accounts.
