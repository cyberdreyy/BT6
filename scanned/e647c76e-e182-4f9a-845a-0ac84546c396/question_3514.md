# Q3514: helpers accept partially hydrated users in getAllUserEmbeddedSolanaWallets.ts

## Question
getAllUserEmbeddedSolanaWallets: filter embedded + solana tolerates a user object missing linked_accounts by returning an empty result; can an attacker exploit a partially hydrated user so a caller believes the user has no wallets and provisions a new one?

## Target
- File/function: [src/utils/getAllUserEmbeddedSolanaWallets.ts](src/utils/getAllUserEmbeddedSolanaWallets.ts) - getAllUserEmbeddedSolanaWallets: filter embedded + solana, sort by wallet_index
- Entrypoint: Solana wallet enumeration
- Attacker controls: linked_accounts contents, duplicate indices
- Exploit idea: Pass a user with linked_accounts undefined.
- Invariant to test: Partially hydrated inputs must raise rather than yield empty results.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a partial user to getAllUserEmbeddedSolanaWallets: filter embedded + solana and assert it raises.
