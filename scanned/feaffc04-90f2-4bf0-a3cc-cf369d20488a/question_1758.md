# Q1758: empty address renders as an empty string in shouldCreateEmbeddedSolWallet.ts

## Question
formatWalletAddress returns '' for undefined; can an attacker cause shouldCreateEmbeddedSolWallet(user to render an empty destination that a user approves as blank or default?

## Target
- File/function: [src/utils/shouldCreateEmbeddedSolWallet.ts](src/utils/shouldCreateEmbeddedSolWallet.ts) - shouldCreateEmbeddedSolWallet(user, createOnLogin)
- Entrypoint: maybeCreateWalletOnLogin after every login
- Attacker controls: linked solana accounts and the createOnLogin setting
- Exploit idea: Pass undefined through the rendering path.
- Invariant to test: Missing values must render as an explicit error, not as empty text.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass undefined to shouldCreateEmbeddedSolWallet(user and assert an explicit marker.
