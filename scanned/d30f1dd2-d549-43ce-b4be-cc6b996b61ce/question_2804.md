# Q2804: psbt forwarded without inspection in EmbeddedBitcoinWalletProvider.ts

## Question
signTransaction forwards the psbt argument verbatim to the iframe; can an attacker submit a psbt through EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes) whose outputs differ from what the app displayed?

## Target
- File/function: [src/embedded/EmbeddedBitcoinWalletProvider.ts](src/embedded/EmbeddedBitcoinWalletProvider.ts) - EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes), signTransaction (psbt), request
- Entrypoint: bitcoinProvider.sign({message}) / .signTransaction({psbt})
- Attacker controls: raw message bytes, psbt hex/base64 payload
- Exploit idea: Submit a psbt with an added output and observe no client-side checks.
- Invariant to test: The SDK must surface or verify the outputs it asks the user to sign.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes) extracts and exposes psbt outputs for confirmation.
