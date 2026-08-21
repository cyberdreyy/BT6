# Q1927: signature appended without verification in offchain-message.ts

## Question
handleSignTransaction calls transaction.addSignature with the base64 signature returned by the signer; can an attacker return a signature for a different message through deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes) so a malformed transaction is broadcast as the user's?

## Target
- File/function: [src/solana/offchain-message.ts](src/solana/offchain-message.ts) - deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes), buildSolanaOffchainMessage (255 + 'solana offchain' preamble, 1232 max), parseSolanaOffchainMessage
- Entrypoint: off-chain message construction for Solana signing
- Attacker controls: origin/domain string, message contents and length, raw bytes handed to the parser
- Exploit idea: Return a valid-looking signature over other bytes and observe it being attached and broadcast.
- Invariant to test: Returned signatures must be verified against the signed message and signer key.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: return a foreign signature to deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes) and assert verification fails before broadcast.
