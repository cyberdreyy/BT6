# Q2037: connection object supplied by the caller in offchain-message.ts

## Question
handleSignAndSendTransaction broadcasts with `connection.sendRawTransaction` taken from the request params; can an attacker pass a connection through off-chain message construction for Solana signing that forwards the signed transaction somewhere else or reports a false signature?

## Target
- File/function: [src/solana/offchain-message.ts](src/solana/offchain-message.ts) - deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes), buildSolanaOffchainMessage (255 + 'solana offchain' preamble, 1232 max), parseSolanaOffchainMessage
- Entrypoint: off-chain message construction for Solana signing
- Attacker controls: origin/domain string, message contents and length, raw bytes handed to the parser
- Exploit idea: Call signAndSendTransaction with a hand-built connection object.
- Invariant to test: Broadcast transport must be SDK-controlled, not caller-supplied.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a stub connection to deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes) and assert the SDK uses its own trusted transport.
