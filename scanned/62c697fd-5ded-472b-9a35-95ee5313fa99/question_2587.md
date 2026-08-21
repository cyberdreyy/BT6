# Q2587: off-chain parser trusts the preamble in offchain-message.ts

## Question
parseSolanaOffchainMessage validates the 0xFF prefix and the 'solana offchain' text but returns version, format and signer bytes unchecked; can an attacker feed bytes through deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes) so the parsed signer public key differs from the actual signer?

## Target
- File/function: [src/solana/offchain-message.ts](src/solana/offchain-message.ts) - deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes), buildSolanaOffchainMessage (255 + 'solana offchain' preamble, 1232 max), parseSolanaOffchainMessage
- Entrypoint: off-chain message construction for Solana signing
- Attacker controls: origin/domain string, message contents and length, raw bytes handed to the parser
- Exploit idea: Parse a crafted buffer with an arbitrary signer field.
- Invariant to test: Parsed signer identity must be verified against the expected signer.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: parse a crafted buffer through deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes) and assert the signer is validated.
