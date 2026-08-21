# Q1751: empty address renders as an empty string in getUserEmbeddedEthereumWallet.ts

## Question
formatWalletAddress returns '' for undefined; can an attacker cause getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0 to render an empty destination that a user approves as blank or default?

## Target
- File/function: [src/utils/getUserEmbeddedEthereumWallet.ts](src/utils/getUserEmbeddedEthereumWallet.ts) - getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0
- Entrypoint: entropy resolution, root-wallet selection, create-on-login checks
- Attacker controls: the user object's linked_accounts array contents and ordering
- Exploit idea: Pass undefined through the rendering path.
- Invariant to test: Missing values must render as an explicit error, not as empty text.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass undefined to getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0 and assert an explicit marker.
