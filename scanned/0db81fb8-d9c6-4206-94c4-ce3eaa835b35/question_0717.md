# Q0717: quantity parser rejects only some shapes in offchain-message.ts

## Question
toQuantity accepts numbers, bigints and 0x-hex but throws otherwise; can an attacker pass a value that survives the check yet decodes differently server-side through deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes)?

## Target
- File/function: [src/solana/offchain-message.ts](src/solana/offchain-message.ts) - deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes), buildSolanaOffchainMessage (255 + 'solana offchain' preamble, 1232 max), parseSolanaOffchainMessage
- Entrypoint: off-chain message construction for Solana signing
- Attacker controls: origin/domain string, message contents and length, raw bytes handed to the parser
- Exploit idea: Feed '0x0000...01', leading-zero hex and oversized values.
- Invariant to test: Quantity encoding must be canonical for every signed field.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: feed a canonicalisation table to deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes) and assert a single normalised output.
