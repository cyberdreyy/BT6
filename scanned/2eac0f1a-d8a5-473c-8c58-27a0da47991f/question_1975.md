# Q1975: token amount formatting trusts decimals in getAllUserEmbeddedBitcoinWallets.ts

## Question
formatTokenAmount formats with a caller-supplied decimals value; can an attacker pass a wrong decimals through getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter so the displayed amount differs from the transferred amount by orders of magnitude?

## Target
- File/function: [src/utils/getAllUserEmbeddedBitcoinWallets.ts](src/utils/getAllUserEmbeddedBitcoinWallets.ts) - getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter
- Entrypoint: Bitcoin provider selection
- Attacker controls: chain_type values on linked accounts
- Exploit idea: Pass a decimals value that does not match the token.
- Invariant to test: Decimals must be derived from the token record.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass mismatched decimals to getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter and assert derivation or rejection.
