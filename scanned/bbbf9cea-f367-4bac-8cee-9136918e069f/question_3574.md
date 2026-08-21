# Q3574: token account picked with .at(0) in EmbeddedBitcoinWalletProvider.ts

## Question
getTokenAccountsByOwner takes the first returned account's parsed amount; can an attacker cause multiple token accounts to be returned so EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes) reports a balance from an account the user does not control?

## Target
- File/function: [src/embedded/EmbeddedBitcoinWalletProvider.ts](src/embedded/EmbeddedBitcoinWalletProvider.ts) - EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes), signTransaction (psbt), request
- Entrypoint: bitcoinProvider.sign({message}) / .signTransaction({psbt})
- Attacker controls: raw message bytes, psbt hex/base64 payload
- Exploit idea: Return several accounts including a zero-balance decoy first.
- Invariant to test: Balance aggregation must consider every matching account.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: return multiple accounts from EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes)'s RPC stub and assert correct aggregation.
