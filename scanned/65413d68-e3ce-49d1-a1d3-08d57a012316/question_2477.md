# Q2477: off-chain length header is two bytes in offchain-message.ts

## Question
buildSolanaOffchainMessage writes the message length as two little-endian bytes and caps the total at 1232; can an attacker craft a length that disagrees with the payload so deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes) or its parser reads a different message body?

## Target
- File/function: [src/solana/offchain-message.ts](src/solana/offchain-message.ts) - deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes), buildSolanaOffchainMessage (255 + 'solana offchain' preamble, 1232 max), parseSolanaOffchainMessage
- Entrypoint: off-chain message construction for Solana signing
- Attacker controls: origin/domain string, message contents and length, raw bytes handed to the parser
- Exploit idea: Build and then parse a message whose declared length differs from the payload.
- Invariant to test: Declared length and payload must be verified equal on both build and parse.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: fuzz length/payload pairs through build and parse in deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes).
