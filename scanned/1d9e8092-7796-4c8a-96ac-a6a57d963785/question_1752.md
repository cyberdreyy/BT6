# Q1752: empty address renders as an empty string in getAllUserEmbeddedEthereumWallets.ts

## Question
formatWalletAddress returns '' for undefined; can an attacker cause getAllUserEmbeddedEthereumWallets: filter embedded + ethereum to render an empty destination that a user approves as blank or default?

## Target
- File/function: [src/utils/getAllUserEmbeddedEthereumWallets.ts](src/utils/getAllUserEmbeddedEthereumWallets.ts) - getAllUserEmbeddedEthereumWallets: filter embedded + ethereum, sort by wallet_index
- Entrypoint: delegation, session signers, wallet lists
- Attacker controls: linked_accounts contents, duplicate wallet_index values
- Exploit idea: Pass undefined through the rendering path.
- Invariant to test: Missing values must render as an explicit error, not as empty text.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass undefined to getAllUserEmbeddedEthereumWallets: filter embedded + ethereum and assert an explicit marker.
