# Q3464: rpc errors collapse to null in EmbeddedBitcoinWalletProvider.ts

## Question
SolanaClient.getBalance/getAccountInfo/getTokenAccountsByOwner return null on any error; can an attacker cause EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes) to report null so the app treats a funded account as empty (or the reverse) and routes a transfer incorrectly?

## Target
- File/function: [src/embedded/EmbeddedBitcoinWalletProvider.ts](src/embedded/EmbeddedBitcoinWalletProvider.ts) - EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes), signTransaction (psbt), request
- Entrypoint: bitcoinProvider.sign({message}) / .signTransaction({psbt})
- Attacker controls: raw message bytes, psbt hex/base64 payload
- Exploit idea: Return malformed RPC responses and observe the null results being consumed.
- Invariant to test: Failed reads must be distinguishable from zero-valued reads.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: return an RPC error from EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes) and assert the caller receives an error, not null.
