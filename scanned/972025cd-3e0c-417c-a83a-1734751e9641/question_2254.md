# Q2254: versioned detection by a property name in EmbeddedBitcoinWalletProvider.ts

## Question
isVersionedTransaction only checks for a 'version' property; can an attacker pass an object carrying that property so EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes) takes the versioned branch on a legacy transaction and serialises the wrong bytes?

## Target
- File/function: [src/embedded/EmbeddedBitcoinWalletProvider.ts](src/embedded/EmbeddedBitcoinWalletProvider.ts) - EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes), signTransaction (psbt), request
- Entrypoint: bitcoinProvider.sign({message}) / .signTransaction({psbt})
- Attacker controls: raw message bytes, psbt hex/base64 payload
- Exploit idea: Pass a legacy transaction object with an added version field.
- Invariant to test: Transaction kind detection must use structural validation.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: pass a spoofed object to EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes) and assert detection is structural.
