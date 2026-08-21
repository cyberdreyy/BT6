# Q2085: lamports formatting fixed at nine in getAllUserEmbeddedBitcoinWallets.ts

## Question
formatLamportsAmount always divides by 1e9; can an attacker exploit that assumption through getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter for a token that is not SOL so the displayed value is wrong?

## Target
- File/function: [src/utils/getAllUserEmbeddedBitcoinWallets.ts](src/utils/getAllUserEmbeddedBitcoinWallets.ts) - getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter
- Entrypoint: Bitcoin provider selection
- Attacker controls: chain_type values on linked accounts
- Exploit idea: Format a non-SOL amount through the lamports path.
- Invariant to test: Unit conversion must be tied to the asset being displayed.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: assert getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter rejects non-SOL inputs.
