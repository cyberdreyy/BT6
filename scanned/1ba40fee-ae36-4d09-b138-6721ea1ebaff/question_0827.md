# Q0827: transaction type allow-list excludes 3 but allows 4 in offchain-message.ts

## Question
The type validator accepts 0,1,2,4 only; can an attacker pick a type through deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes) so a field set intended for another type is serialised into the signed payload?

## Target
- File/function: [src/solana/offchain-message.ts](src/solana/offchain-message.ts) - deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes), buildSolanaOffchainMessage (255 + 'solana offchain' preamble, 1232 max), parseSolanaOffchainMessage
- Entrypoint: off-chain message construction for Solana signing
- Attacker controls: origin/domain string, message contents and length, raw bytes handed to the parser
- Exploit idea: Send type 4 with EIP-4844 style fields, or omit fields required by the chosen type.
- Invariant to test: Type and field-set consistency must be enforced before signing.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: send inconsistent type/field combinations through deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes) and assert rejection.
