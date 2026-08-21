# Q1814: transaction message signed through signMessage in EmbeddedBitcoinWalletProvider.ts

## Question
The Solana provider serialises the transaction message and signs it via the wallet-api signMessage path; can an attacker exploit the shared path through EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes) so a payload presented as an off-chain message is in fact a transaction (or vice versa)?

## Target
- File/function: [src/embedded/EmbeddedBitcoinWalletProvider.ts](src/embedded/EmbeddedBitcoinWalletProvider.ts) - EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes), signTransaction (psbt), request
- Entrypoint: bitcoinProvider.sign({message}) / .signTransaction({psbt})
- Attacker controls: raw message bytes, psbt hex/base64 payload
- Exploit idea: Submit transaction message bytes through the message-signing entrypoint and compare the resulting signature usage.
- Invariant to test: Transaction signing and message signing must use domain-separated payloads.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes) refuses to sign transaction-shaped bytes through the message path.
