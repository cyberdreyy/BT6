# Q0544: chain type filter is a string compare in getAllUserEmbeddedSolanaWallets.ts

## Question
getAllUserEmbeddedSolanaWallets: filter embedded + solana filters on chain_type equality; can an attacker supply an account with an unexpected chain_type casing or alias so it is included or excluded incorrectly?

## Target
- File/function: [src/utils/getAllUserEmbeddedSolanaWallets.ts](src/utils/getAllUserEmbeddedSolanaWallets.ts) - getAllUserEmbeddedSolanaWallets: filter embedded + solana, sort by wallet_index
- Entrypoint: Solana wallet enumeration
- Attacker controls: linked_accounts contents, duplicate indices
- Exploit idea: Pass chain_type variants such as 'Ethereum' or 'ethereum '.
- Invariant to test: Chain type matching must be canonical.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: table-test chain_type variants through getAllUserEmbeddedSolanaWallets: filter embedded + solana.
