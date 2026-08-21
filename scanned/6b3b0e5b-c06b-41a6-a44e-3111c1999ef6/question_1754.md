# Q1754: empty address renders as an empty string in getAllUserEmbeddedSolanaWallets.ts

## Question
formatWalletAddress returns '' for undefined; can an attacker cause getAllUserEmbeddedSolanaWallets: filter embedded + solana to render an empty destination that a user approves as blank or default?

## Target
- File/function: [src/utils/getAllUserEmbeddedSolanaWallets.ts](src/utils/getAllUserEmbeddedSolanaWallets.ts) - getAllUserEmbeddedSolanaWallets: filter embedded + solana, sort by wallet_index
- Entrypoint: Solana wallet enumeration
- Attacker controls: linked_accounts contents, duplicate indices
- Exploit idea: Pass undefined through the rendering path.
- Invariant to test: Missing values must render as an explicit error, not as empty text.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass undefined to getAllUserEmbeddedSolanaWallets: filter embedded + solana and assert an explicit marker.
