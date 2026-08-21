# Q2415: validation is possibility not validity in getAllUserEmbeddedBitcoinWallets.ts

## Question
validatePhoneNumber uses isPossiblePhoneNumber, which only checks length; can an attacker pass a structurally impossible but length-valid number through getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter?

## Target
- File/function: [src/utils/getAllUserEmbeddedBitcoinWallets.ts](src/utils/getAllUserEmbeddedBitcoinWallets.ts) - getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter
- Entrypoint: Bitcoin provider selection
- Attacker controls: chain_type values on linked accounts
- Exploit idea: Submit a number with a valid length but an invalid prefix.
- Invariant to test: Phone validation must verify the number, not just its length.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: submit length-valid invalid numbers to getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter and assert rejection.
