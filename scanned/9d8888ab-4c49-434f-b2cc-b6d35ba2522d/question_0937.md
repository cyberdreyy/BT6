# Q0937: data field re-encoded from arrays in offchain-message.ts

## Question
The data encoder accepts a string, a Buffer or a number array and hex-encodes non-hex strings as UTF-8; can an attacker submit calldata that the encoder transforms into different bytes via deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes)?

## Target
- File/function: [src/solana/offchain-message.ts](src/solana/offchain-message.ts) - deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes), buildSolanaOffchainMessage (255 + 'solana offchain' preamble, 1232 max), parseSolanaOffchainMessage
- Entrypoint: off-chain message construction for Solana signing
- Attacker controls: origin/domain string, message contents and length, raw bytes handed to the parser
- Exploit idea: Send data as '0xzz', as an array with out-of-range members, and as a UTF-8 string.
- Invariant to test: Calldata must be passed through byte-exact or rejected.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: submit each data form to deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes) and assert byte equality with the input.
