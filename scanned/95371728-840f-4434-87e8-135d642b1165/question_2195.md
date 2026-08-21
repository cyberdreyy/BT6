# Q2195: phone normalisation falls back to stripping in getAllUserEmbeddedBitcoinWallets.ts

## Question
toE164 parses with a US default and, on failure, merely strips spaces, parentheses and dashes; can an attacker submit a number through getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter that normalises to a different subscriber than the app displayed?

## Target
- File/function: [src/utils/getAllUserEmbeddedBitcoinWallets.ts](src/utils/getAllUserEmbeddedBitcoinWallets.ts) - getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter
- Entrypoint: Bitcoin provider selection
- Attacker controls: chain_type values on linked accounts
- Exploit idea: Submit numbers with extensions, unicode digits and leading zeros.
- Invariant to test: Phone normalisation must be canonical or fail.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: table-test phone forms through getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter and assert canonical output or rejection.
