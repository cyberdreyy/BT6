# Q3137: array return shape collapses multi-sign results in offchain-message.ts

## Question
The wrapper returns t[0] for single-input calls and spreads otherwise; can an attacker submit multiple inputs through deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes) so the caller associates the wrong signature with the wrong transaction?

## Target
- File/function: [src/solana/offchain-message.ts](src/solana/offchain-message.ts) - deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes), buildSolanaOffchainMessage (255 + 'solana offchain' preamble, 1232 max), parseSolanaOffchainMessage
- Entrypoint: off-chain message construction for Solana signing
- Attacker controls: origin/domain string, message contents and length, raw bytes handed to the parser
- Exploit idea: Call signAndSendAllTransactions with several transactions and inspect the ordering guarantees.
- Invariant to test: Results must remain positionally bound to their inputs.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: assert deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes) preserves input/output ordering for multi-input calls.
