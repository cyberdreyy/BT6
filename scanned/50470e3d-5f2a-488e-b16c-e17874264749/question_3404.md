# Q3404: selection result feeds funding destinations in getAllUserEmbeddedSolanaWallets.ts

## Question
Funding and deposit flows use getAllUserEmbeddedSolanaWallets: filter embedded + solana to choose a destination; can an attacker influence the selection so funds arrive at a wallet the user did not intend?

## Target
- File/function: [src/utils/getAllUserEmbeddedSolanaWallets.ts](src/utils/getAllUserEmbeddedSolanaWallets.ts) - getAllUserEmbeddedSolanaWallets: filter embedded + solana, sort by wallet_index
- Entrypoint: Solana wallet enumeration
- Attacker controls: linked_accounts contents, duplicate indices
- Exploit idea: Change the account set before a funding flow.
- Invariant to test: Funding destinations must be user-confirmed, not derived by a helper.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Integration test: change accounts before funding and assert confirmation is re-requested.
