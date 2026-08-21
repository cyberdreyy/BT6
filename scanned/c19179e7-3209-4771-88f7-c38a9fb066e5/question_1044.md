# Q1044: access list normalisation drops entries in EmbeddedBitcoinWalletProvider.ts

## Question
toAccessList handles arrays, tuple pairs and objects; can an attacker craft an access list through EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes) that is silently reshaped so the signed transaction differs from the approved one?

## Target
- File/function: [src/embedded/EmbeddedBitcoinWalletProvider.ts](src/embedded/EmbeddedBitcoinWalletProvider.ts) - EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes), signTransaction (psbt), request
- Entrypoint: bitcoinProvider.sign({message}) / .signTransaction({psbt})
- Attacker controls: raw message bytes, psbt hex/base64 payload
- Exploit idea: Send an access list in each accepted shape and compare the serialised result.
- Invariant to test: Access-list normalisation must be lossless.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: round-trip every access-list shape through EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes).
