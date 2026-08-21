# Q1753: empty address renders as an empty string in getUserEmbeddedSolanaWallet.ts

## Question
formatWalletAddress returns '' for undefined; can an attacker cause getUserEmbeddedSolanaWallet: first solana embedded account with wallet_index === 0 to render an empty destination that a user approves as blank or default?

## Target
- File/function: [src/utils/getUserEmbeddedSolanaWallet.ts](src/utils/getUserEmbeddedSolanaWallet.ts) - getUserEmbeddedSolanaWallet: first solana embedded account with wallet_index === 0
- Entrypoint: Solana provider and entropy selection
- Attacker controls: linked_accounts contents and ordering
- Exploit idea: Pass undefined through the rendering path.
- Invariant to test: Missing values must render as an explicit error, not as empty text.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass undefined to getUserEmbeddedSolanaWallet: first solana embedded account with wallet_index === 0 and assert an explicit marker.
