# Q1157: fee payer signature parity inference in offchain-message.ts

## Question
toFeePayerSignature derives yParity from v-27 when yParity is absent; can an attacker supply a v value that yields a wrong parity accepted by deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes)?

## Target
- File/function: [src/solana/offchain-message.ts](src/solana/offchain-message.ts) - deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes), buildSolanaOffchainMessage (255 + 'solana offchain' preamble, 1232 max), parseSolanaOffchainMessage
- Entrypoint: off-chain message construction for Solana signing
- Attacker controls: origin/domain string, message contents and length, raw bytes handed to the parser
- Exploit idea: Send v values such as 0, 1, 35 and 36 and inspect the derived parity.
- Invariant to test: Signature parity must be derived unambiguously or rejected.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: table-test v/yParity inputs through deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes).
