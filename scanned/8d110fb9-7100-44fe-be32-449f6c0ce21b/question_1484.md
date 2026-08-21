# Q1484: typed data domain unchecked against the chain in EmbeddedBitcoinWalletProvider.ts

## Question
The domain (chainId, verifyingContract) is forwarded verbatim; can an attacker sign typed data whose domain chainId differs from the provider chain via EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes), producing a signature valid on another chain?

## Target
- File/function: [src/embedded/EmbeddedBitcoinWalletProvider.ts](src/embedded/EmbeddedBitcoinWalletProvider.ts) - EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes), signTransaction (psbt), request
- Entrypoint: bitcoinProvider.sign({message}) / .signTransaction({psbt})
- Attacker controls: raw message bytes, psbt hex/base64 payload
- Exploit idea: Submit typed data with a foreign domain.chainId while the provider is on mainnet.
- Invariant to test: The typed-data domain must agree with the provider's active chain.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: submit a mismatched domain chainId to EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes) and assert rejection.
