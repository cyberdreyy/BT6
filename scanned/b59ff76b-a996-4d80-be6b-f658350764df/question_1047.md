# Q1047: access list normalisation drops entries in offchain-message.ts

## Question
toAccessList handles arrays, tuple pairs and objects; can an attacker craft an access list through deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes) that is silently reshaped so the signed transaction differs from the approved one?

## Target
- File/function: [src/solana/offchain-message.ts](src/solana/offchain-message.ts) - deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes), buildSolanaOffchainMessage (255 + 'solana offchain' preamble, 1232 max), parseSolanaOffchainMessage
- Entrypoint: off-chain message construction for Solana signing
- Attacker controls: origin/domain string, message contents and length, raw bytes handed to the parser
- Exploit idea: Send an access list in each accepted shape and compare the serialised result.
- Invariant to test: Access-list normalisation must be lossless.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: round-trip every access-list shape through deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes).
