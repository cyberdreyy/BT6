# Q1861: wei formatting strips trailing digits in getUserEmbeddedEthereumWallet.ts

## Question
formatWeiAmount fixes to three decimals and strips trailing zeros and dots; can an attacker choose an amount so getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0 displays a materially smaller value than will be signed?

## Target
- File/function: [src/utils/getUserEmbeddedEthereumWallet.ts](src/utils/getUserEmbeddedEthereumWallet.ts) - getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0
- Entrypoint: entropy resolution, root-wallet selection, create-on-login checks
- Attacker controls: the user object's linked_accounts array contents and ordering
- Exploit idea: Format values just below the display precision.
- Invariant to test: Displayed amounts must never round down the value being approved.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: assert getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0 never displays less than the true amount.
