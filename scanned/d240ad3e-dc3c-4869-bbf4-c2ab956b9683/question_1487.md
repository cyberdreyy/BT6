# Q1487: typed data domain unchecked against the chain in offchain-message.ts

## Question
The domain (chainId, verifyingContract) is forwarded verbatim; can an attacker sign typed data whose domain chainId differs from the provider chain via deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes), producing a signature valid on another chain?

## Target
- File/function: [src/solana/offchain-message.ts](src/solana/offchain-message.ts) - deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes), buildSolanaOffchainMessage (255 + 'solana offchain' preamble, 1232 max), parseSolanaOffchainMessage
- Entrypoint: off-chain message construction for Solana signing
- Attacker controls: origin/domain string, message contents and length, raw bytes handed to the parser
- Exploit idea: Submit typed data with a foreign domain.chainId while the provider is on mainnet.
- Invariant to test: The typed-data domain must agree with the provider's active chain.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: submit a mismatched domain chainId to deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes) and assert rejection.
