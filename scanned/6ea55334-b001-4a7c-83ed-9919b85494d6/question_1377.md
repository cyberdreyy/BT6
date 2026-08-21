# Q1377: typed data accepted as a JSON string in offchain-message.ts

## Question
toWalletApiTypedData JSON.parses string input before use; can an attacker pass a string whose parse result differs from what the app displayed, so deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes) signs different typed data?

## Target
- File/function: [src/solana/offchain-message.ts](src/solana/offchain-message.ts) - deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes), buildSolanaOffchainMessage (255 + 'solana offchain' preamble, 1232 max), parseSolanaOffchainMessage
- Entrypoint: off-chain message construction for Solana signing
- Attacker controls: origin/domain string, message contents and length, raw bytes handed to the parser
- Exploit idea: Pass a JSON string with duplicate keys or unusual escaping and compare the parsed structure.
- Invariant to test: String and object inputs must produce identical, validated structures.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: pass duplicate-key JSON to deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes) and assert deterministic, validated parsing.
