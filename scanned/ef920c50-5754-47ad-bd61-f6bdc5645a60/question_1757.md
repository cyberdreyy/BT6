# Q1757: empty address renders as an empty string in shouldCreateEmbeddedEthWallet.ts

## Question
formatWalletAddress returns '' for undefined; can an attacker cause shouldCreateEmbeddedEthWallet(user to render an empty destination that a user approves as blank or default?

## Target
- File/function: [src/utils/shouldCreateEmbeddedEthWallet.ts](src/utils/shouldCreateEmbeddedEthWallet.ts) - shouldCreateEmbeddedEthWallet(user, createOnLogin: 'off'|'users-without-wallets'|'all-users')
- Entrypoint: maybeCreateWalletOnLogin after every login
- Attacker controls: external wallets linked to the account and the createOnLogin setting
- Exploit idea: Pass undefined through the rendering path.
- Invariant to test: Missing values must render as an explicit error, not as empty text.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass undefined to shouldCreateEmbeddedEthWallet(user and assert an explicit marker.
