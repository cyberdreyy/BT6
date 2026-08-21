# Q3467: rpc errors collapse to null in offchain-message.ts

## Question
SolanaClient.getBalance/getAccountInfo/getTokenAccountsByOwner return null on any error; can an attacker cause deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes) to report null so the app treats a funded account as empty (or the reverse) and routes a transfer incorrectly?

## Target
- File/function: [src/solana/offchain-message.ts](src/solana/offchain-message.ts) - deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes), buildSolanaOffchainMessage (255 + 'solana offchain' preamble, 1232 max), parseSolanaOffchainMessage
- Entrypoint: off-chain message construction for Solana signing
- Attacker controls: origin/domain string, message contents and length, raw bytes handed to the parser
- Exploit idea: Return malformed RPC responses and observe the null results being consumed.
- Invariant to test: Failed reads must be distinguishable from zero-valued reads.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: return an RPC error from deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes) and assert the caller receives an error, not null.
