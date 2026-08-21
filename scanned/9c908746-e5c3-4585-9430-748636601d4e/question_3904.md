# Q3904: tempo path selected by a predicate on the request in EmbeddedBitcoinWalletProvider.ts

## Question
The provider routes to the Tempo serializer when isTempoTransactionRequest matches; can an attacker shape a request so EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes) takes the Tempo path on a non-Tempo chain, or the standard path for a Tempo transaction?

## Target
- File/function: [src/embedded/EmbeddedBitcoinWalletProvider.ts](src/embedded/EmbeddedBitcoinWalletProvider.ts) - EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes), signTransaction (psbt), request
- Entrypoint: bitcoinProvider.sign({message}) / .signTransaction({psbt})
- Attacker controls: raw message bytes, psbt hex/base64 payload
- Exploit idea: Submit hybrid field sets and compare the serialised output to the target chain.
- Invariant to test: Serializer selection must agree with the target chain and be rejected otherwise.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: submit hybrid requests to EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes) and assert consistent routing.
