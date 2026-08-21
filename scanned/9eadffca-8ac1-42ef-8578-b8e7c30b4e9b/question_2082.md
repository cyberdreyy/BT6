# Q2082: lamports formatting fixed at nine in getAllUserEmbeddedEthereumWallets.ts

## Question
formatLamportsAmount always divides by 1e9; can an attacker exploit that assumption through getAllUserEmbeddedEthereumWallets: filter embedded + ethereum for a token that is not SOL so the displayed value is wrong?

## Target
- File/function: [src/utils/getAllUserEmbeddedEthereumWallets.ts](src/utils/getAllUserEmbeddedEthereumWallets.ts) - getAllUserEmbeddedEthereumWallets: filter embedded + ethereum, sort by wallet_index
- Entrypoint: delegation, session signers, wallet lists
- Attacker controls: linked_accounts contents, duplicate wallet_index values
- Exploit idea: Format a non-SOL amount through the lamports path.
- Invariant to test: Unit conversion must be tied to the asset being displayed.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: assert getAllUserEmbeddedEthereumWallets: filter embedded + ethereum rejects non-SOL inputs.
