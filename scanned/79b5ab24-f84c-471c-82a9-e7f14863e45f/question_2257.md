# Q2257: versioned detection by a property name in offchain-message.ts

## Question
isVersionedTransaction only checks for a 'version' property; can an attacker pass an object carrying that property so deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes) takes the versioned branch on a legacy transaction and serialises the wrong bytes?

## Target
- File/function: [src/solana/offchain-message.ts](src/solana/offchain-message.ts) - deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes), buildSolanaOffchainMessage (255 + 'solana offchain' preamble, 1232 max), parseSolanaOffchainMessage
- Entrypoint: off-chain message construction for Solana signing
- Attacker controls: origin/domain string, message contents and length, raw bytes handed to the parser
- Exploit idea: Pass a legacy transaction object with an added version field.
- Invariant to test: Transaction kind detection must use structural validation.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: pass a spoofed object to deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes) and assert detection is structural.
