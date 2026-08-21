# Q2474: off-chain length header is two bytes in EmbeddedBitcoinWalletProvider.ts

## Question
buildSolanaOffchainMessage writes the message length as two little-endian bytes and caps the total at 1232; can an attacker craft a length that disagrees with the payload so EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes) or its parser reads a different message body?

## Target
- File/function: [src/embedded/EmbeddedBitcoinWalletProvider.ts](src/embedded/EmbeddedBitcoinWalletProvider.ts) - EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes), signTransaction (psbt), request
- Entrypoint: bitcoinProvider.sign({message}) / .signTransaction({psbt})
- Attacker controls: raw message bytes, psbt hex/base64 payload
- Exploit idea: Build and then parse a message whose declared length differs from the payload.
- Invariant to test: Declared length and payload must be verified equal on both build and parse.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: fuzz length/payload pairs through build and parse in EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes).
