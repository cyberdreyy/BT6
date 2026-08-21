# Q2525: masked display reveals the last four in getAllUserEmbeddedBitcoinWallets.ts

## Question
lastFourDigits renders '*' plus the final four digits; can an attacker use a surface fed by getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter to confirm a victim's phone number by comparing masks?

## Target
- File/function: [src/utils/getAllUserEmbeddedBitcoinWallets.ts](src/utils/getAllUserEmbeddedBitcoinWallets.ts) - getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter
- Entrypoint: Bitcoin provider selection
- Attacker controls: chain_type values on linked accounts
- Exploit idea: Compare masks across candidate numbers.
- Invariant to test: Masked identifiers must not confirm guesses.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: assert getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter's mask does not distinguish candidate numbers by four digits alone.
