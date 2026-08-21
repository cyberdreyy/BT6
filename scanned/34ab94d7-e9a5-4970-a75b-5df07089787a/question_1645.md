# Q1645: address rendering truncates the middle in getAllUserEmbeddedBitcoinWallets.ts

## Question
formatWalletAddress shows five leading and four trailing characters; can an attacker generate an address that renders identically to the victim's expected address so a confirmation screen fed by getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter shows the wrong destination as correct?

## Target
- File/function: [src/utils/getAllUserEmbeddedBitcoinWallets.ts](src/utils/getAllUserEmbeddedBitcoinWallets.ts) - getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter
- Entrypoint: Bitcoin provider selection
- Attacker controls: chain_type values on linked accounts
- Exploit idea: Grind an address sharing the displayed prefix and suffix and compare renderings.
- Invariant to test: Confirmation surfaces must show enough of the address to be unambiguous.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: assert two distinct addresses never share a getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter rendering.
