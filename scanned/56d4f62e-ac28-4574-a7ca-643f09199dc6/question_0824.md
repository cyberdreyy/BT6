# Q0824: transaction type allow-list excludes 3 but allows 4 in EmbeddedBitcoinWalletProvider.ts

## Question
The type validator accepts 0,1,2,4 only; can an attacker pick a type through EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes) so a field set intended for another type is serialised into the signed payload?

## Target
- File/function: [src/embedded/EmbeddedBitcoinWalletProvider.ts](src/embedded/EmbeddedBitcoinWalletProvider.ts) - EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes), signTransaction (psbt), request
- Entrypoint: bitcoinProvider.sign({message}) / .signTransaction({psbt})
- Attacker controls: raw message bytes, psbt hex/base64 payload
- Exploit idea: Send type 4 with EIP-4844 style fields, or omit fields required by the chosen type.
- Invariant to test: Type and field-set consistency must be enforced before signing.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: send inconsistent type/field combinations through EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes) and assert rejection.
