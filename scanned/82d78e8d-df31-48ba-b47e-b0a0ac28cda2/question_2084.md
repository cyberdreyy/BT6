# Q2084: lamports formatting fixed at nine in getAllUserEmbeddedSolanaWallets.ts

## Question
formatLamportsAmount always divides by 1e9; can an attacker exploit that assumption through getAllUserEmbeddedSolanaWallets: filter embedded + solana for a token that is not SOL so the displayed value is wrong?

## Target
- File/function: [src/utils/getAllUserEmbeddedSolanaWallets.ts](src/utils/getAllUserEmbeddedSolanaWallets.ts) - getAllUserEmbeddedSolanaWallets: filter embedded + solana, sort by wallet_index
- Entrypoint: Solana wallet enumeration
- Attacker controls: linked_accounts contents, duplicate indices
- Exploit idea: Format a non-SOL amount through the lamports path.
- Invariant to test: Unit conversion must be tied to the asset being displayed.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: assert getAllUserEmbeddedSolanaWallets: filter embedded + solana rejects non-SOL inputs.
