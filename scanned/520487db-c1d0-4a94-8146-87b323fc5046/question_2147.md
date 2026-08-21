# Q2147: options forwarded to the broadcaster in offchain-message.ts

## Question
The options argument is passed to sendRawTransaction unchecked; can an attacker set options through deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes) that suppress preflight and hide a failing or malicious transaction?

## Target
- File/function: [src/solana/offchain-message.ts](src/solana/offchain-message.ts) - deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes), buildSolanaOffchainMessage (255 + 'solana offchain' preamble, 1232 max), parseSolanaOffchainMessage
- Entrypoint: off-chain message construction for Solana signing
- Attacker controls: origin/domain string, message contents and length, raw bytes handed to the parser
- Exploit idea: Send skipPreflight and non-default commitment values.
- Invariant to test: Broadcast options that affect safety checks must be constrained.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes) pins preflight-relevant options.
