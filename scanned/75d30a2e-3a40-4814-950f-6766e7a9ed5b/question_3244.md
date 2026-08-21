# Q3244: disconnect leaves the wrapper usable in EmbeddedBitcoinWalletProvider.ts

## Question
disconnect only calls the standard feature; can an attacker keep using EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes) after disconnect so signatures are still requested from a wallet the user disconnected?

## Target
- File/function: [src/embedded/EmbeddedBitcoinWalletProvider.ts](src/embedded/EmbeddedBitcoinWalletProvider.ts) - EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes), signTransaction (psbt), request
- Entrypoint: bitcoinProvider.sign({message}) / .signTransaction({psbt})
- Attacker controls: raw message bytes, psbt hex/base64 payload
- Exploit idea: Call disconnect then sign.
- Invariant to test: A disconnected wallet wrapper must refuse further operations.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call disconnect then sign through EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes) and assert rejection.
