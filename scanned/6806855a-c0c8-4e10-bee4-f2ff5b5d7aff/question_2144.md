# Q2144: options forwarded to the broadcaster in EmbeddedBitcoinWalletProvider.ts

## Question
The options argument is passed to sendRawTransaction unchecked; can an attacker set options through EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes) that suppress preflight and hide a failing or malicious transaction?

## Target
- File/function: [src/embedded/EmbeddedBitcoinWalletProvider.ts](src/embedded/EmbeddedBitcoinWalletProvider.ts) - EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes), signTransaction (psbt), request
- Entrypoint: bitcoinProvider.sign({message}) / .signTransaction({psbt})
- Attacker controls: raw message bytes, psbt hex/base64 payload
- Exploit idea: Send skipPreflight and non-default commitment values.
- Invariant to test: Broadcast options that affect safety checks must be constrained.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes) pins preflight-relevant options.
