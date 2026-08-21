# Q0714: quantity parser rejects only some shapes in EmbeddedBitcoinWalletProvider.ts

## Question
toQuantity accepts numbers, bigints and 0x-hex but throws otherwise; can an attacker pass a value that survives the check yet decodes differently server-side through EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes)?

## Target
- File/function: [src/embedded/EmbeddedBitcoinWalletProvider.ts](src/embedded/EmbeddedBitcoinWalletProvider.ts) - EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes), signTransaction (psbt), request
- Entrypoint: bitcoinProvider.sign({message}) / .signTransaction({psbt})
- Attacker controls: raw message bytes, psbt hex/base64 payload
- Exploit idea: Feed '0x0000...01', leading-zero hex and oversized values.
- Invariant to test: Quantity encoding must be canonical for every signed field.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: feed a canonicalisation table to EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes) and assert a single normalised output.
