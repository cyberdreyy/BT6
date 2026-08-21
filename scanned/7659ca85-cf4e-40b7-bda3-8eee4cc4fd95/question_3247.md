# Q3247: disconnect leaves the wrapper usable in offchain-message.ts

## Question
disconnect only calls the standard feature; can an attacker keep using deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes) after disconnect so signatures are still requested from a wallet the user disconnected?

## Target
- File/function: [src/solana/offchain-message.ts](src/solana/offchain-message.ts) - deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes), buildSolanaOffchainMessage (255 + 'solana offchain' preamble, 1232 max), parseSolanaOffchainMessage
- Entrypoint: off-chain message construction for Solana signing
- Attacker controls: origin/domain string, message contents and length, raw bytes handed to the parser
- Exploit idea: Call disconnect then sign.
- Invariant to test: A disconnected wallet wrapper must refuse further operations.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call disconnect then sign through deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes) and assert rejection.
