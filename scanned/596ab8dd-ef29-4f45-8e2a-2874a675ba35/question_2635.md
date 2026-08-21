# Q2635: typing formatter leaks partial state in getAllUserEmbeddedBitcoinWallets.ts

## Question
phoneNumberTypingFormatter builds an AsYouType instance per country; can an attacker exploit its retained state through getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter so a previously typed number influences the formatted result of the next?

## Target
- File/function: [src/utils/getAllUserEmbeddedBitcoinWallets.ts](src/utils/getAllUserEmbeddedBitcoinWallets.ts) - getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter
- Entrypoint: Bitcoin provider selection
- Attacker controls: chain_type values on linked accounts
- Exploit idea: Reuse the formatter across two inputs.
- Invariant to test: Formatter state must not persist between inputs.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: reuse a getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter formatter and assert no state carries over.
