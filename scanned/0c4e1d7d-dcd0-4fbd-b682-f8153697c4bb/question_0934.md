# Q0934: data field re-encoded from arrays in EmbeddedBitcoinWalletProvider.ts

## Question
The data encoder accepts a string, a Buffer or a number array and hex-encodes non-hex strings as UTF-8; can an attacker submit calldata that the encoder transforms into different bytes via EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes)?

## Target
- File/function: [src/embedded/EmbeddedBitcoinWalletProvider.ts](src/embedded/EmbeddedBitcoinWalletProvider.ts) - EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes), signTransaction (psbt), request
- Entrypoint: bitcoinProvider.sign({message}) / .signTransaction({psbt})
- Attacker controls: raw message bytes, psbt hex/base64 payload
- Exploit idea: Send data as '0xzz', as an array with out-of-range members, and as a UTF-8 string.
- Invariant to test: Calldata must be passed through byte-exact or rejected.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: submit each data form to EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes) and assert byte equality with the input.
