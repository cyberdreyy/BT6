# Q0604: bigint stringification changes values in EmbeddedBitcoinWalletProvider.ts

## Question
handleSignTransaction converts bigint fields with toHex over Object.keys, including nested call values; can an attacker craft a field whose conversion is lossy so EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes) signs a different value than displayed?

## Target
- File/function: [src/embedded/EmbeddedBitcoinWalletProvider.ts](src/embedded/EmbeddedBitcoinWalletProvider.ts) - EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes), signTransaction (psbt), request
- Entrypoint: bitcoinProvider.sign({message}) / .signTransaction({psbt})
- Attacker controls: raw message bytes, psbt hex/base64 payload
- Exploit idea: Submit values at the edges of the bigint/number/hex conversions and diff the serialised output.
- Invariant to test: Numeric conversion must be exact and total for every signed field.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: property-test numeric fields through EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes) and assert round-trip equality.
