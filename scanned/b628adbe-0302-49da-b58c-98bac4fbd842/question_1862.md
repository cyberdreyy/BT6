# Q1862: wei formatting strips trailing digits in getAllUserEmbeddedEthereumWallets.ts

## Question
formatWeiAmount fixes to three decimals and strips trailing zeros and dots; can an attacker choose an amount so getAllUserEmbeddedEthereumWallets: filter embedded + ethereum displays a materially smaller value than will be signed?

## Target
- File/function: [src/utils/getAllUserEmbeddedEthereumWallets.ts](src/utils/getAllUserEmbeddedEthereumWallets.ts) - getAllUserEmbeddedEthereumWallets: filter embedded + ethereum, sort by wallet_index
- Entrypoint: delegation, session signers, wallet lists
- Attacker controls: linked_accounts contents, duplicate wallet_index values
- Exploit idea: Format values just below the display precision.
- Invariant to test: Displayed amounts must never round down the value being approved.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: assert getAllUserEmbeddedEthereumWallets: filter embedded + ethereum never displays less than the true amount.
