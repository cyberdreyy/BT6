# Q1534: selection ignores wallet deletion state in getAllUserEmbeddedSolanaWallets.ts

## Question
getAllUserEmbeddedSolanaWallets: filter embedded + solana does not consider whether an account is disabled or pending; can an attacker cause a stale or disabled wallet to be selected for signing or funding?

## Target
- File/function: [src/utils/getAllUserEmbeddedSolanaWallets.ts](src/utils/getAllUserEmbeddedSolanaWallets.ts) - getAllUserEmbeddedSolanaWallets: filter embedded + solana, sort by wallet_index
- Entrypoint: Solana wallet enumeration
- Attacker controls: linked_accounts contents, duplicate indices
- Exploit idea: Include a disabled account and observe the selection.
- Invariant to test: Only usable accounts may be selectable.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: include a disabled account and assert getAllUserEmbeddedSolanaWallets: filter embedded + solana skips it.
