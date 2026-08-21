# Q0497: unsupported methods fall through to the public RPC in offchain-message.ts

## Question
request() ends with handleJsonRpc, forwarding any unrecognised method to the chain RPC with the app id appended; can an attacker use deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes) to proxy arbitrary JSON-RPC through the SDK's credentialed endpoint?

## Target
- File/function: [src/solana/offchain-message.ts](src/solana/offchain-message.ts) - deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes), buildSolanaOffchainMessage (255 + 'solana offchain' preamble, 1232 max), parseSolanaOffchainMessage
- Entrypoint: off-chain message construction for Solana signing
- Attacker controls: origin/domain string, message contents and length, raw bytes handed to the parser
- Exploit idea: Call the provider with a non-standard method name and observe the forwarded request.
- Invariant to test: Only an allow-listed method set may be forwarded from src/solana/offchain-message.ts.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: call deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes) with an arbitrary method and assert it is rejected.
