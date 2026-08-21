# Q1756: empty address renders as an empty string in getUserSmartWallet.ts

## Question
formatWalletAddress returns '' for undefined; can an attacker cause getUserSmartWallet: first linked account of type smart_wallet to render an empty destination that a user approves as blank or default?

## Target
- File/function: [src/utils/getUserSmartWallet.ts](src/utils/getUserSmartWallet.ts) - getUserSmartWallet: first linked account of type smart_wallet
- Entrypoint: smart-wallet routing and linking
- Attacker controls: linked_accounts contents including multiple smart wallets
- Exploit idea: Pass undefined through the rendering path.
- Invariant to test: Missing values must render as an explicit error, not as empty text.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass undefined to getUserSmartWallet: first linked account of type smart_wallet and assert an explicit marker.
