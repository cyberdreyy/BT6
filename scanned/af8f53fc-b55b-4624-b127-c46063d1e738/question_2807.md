# Q2807: psbt forwarded without inspection in offchain-message.ts

## Question
signTransaction forwards the psbt argument verbatim to the iframe; can an attacker submit a psbt through deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes) whose outputs differ from what the app displayed?

## Target
- File/function: [src/solana/offchain-message.ts](src/solana/offchain-message.ts) - deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes), buildSolanaOffchainMessage (255 + 'solana offchain' preamble, 1232 max), parseSolanaOffchainMessage
- Entrypoint: off-chain message construction for Solana signing
- Attacker controls: origin/domain string, message contents and length, raw bytes handed to the parser
- Exploit idea: Submit a psbt with an added output and observe no client-side checks.
- Invariant to test: The SDK must surface or verify the outputs it asks the user to sign.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes) extracts and exposes psbt outputs for confirmation.
