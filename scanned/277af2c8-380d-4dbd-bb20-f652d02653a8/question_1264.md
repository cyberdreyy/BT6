# Q1264: typed data primaryType coerced with String() in EmbeddedBitcoinWalletProvider.ts

## Question
toWalletApiTypedData sets primary_type via String(typedData.primaryType) and passes types/domain/message straight through; can an attacker supply a primaryType object whose toString names a different struct so EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes) signs a payload with a mismatched type?

## Target
- File/function: [src/embedded/EmbeddedBitcoinWalletProvider.ts](src/embedded/EmbeddedBitcoinWalletProvider.ts) - EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes), signTransaction (psbt), request
- Entrypoint: bitcoinProvider.sign({message}) / .signTransaction({psbt})
- Attacker controls: raw message bytes, psbt hex/base64 payload
- Exploit idea: Pass an object with a custom toString as primaryType.
- Invariant to test: The primary type must be a validated key of the supplied types map.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: pass a non-string primaryType to EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes) and assert rejection.
