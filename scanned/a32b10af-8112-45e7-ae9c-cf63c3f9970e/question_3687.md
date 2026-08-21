# Q3687: chain id switch emits an event apps trust in offchain-message.ts

## Question
internalSwitchEthereumChain emits chainChanged after mutating internal state; can an attacker force a switch through deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes) so the app's UI shows one chain while signing occurs on another?

## Target
- File/function: [src/solana/offchain-message.ts](src/solana/offchain-message.ts) - deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes), buildSolanaOffchainMessage (255 + 'solana offchain' preamble, 1232 max), parseSolanaOffchainMessage
- Entrypoint: off-chain message construction for Solana signing
- Attacker controls: origin/domain string, message contents and length, raw bytes handed to the parser
- Exploit idea: Trigger a switch during a pending signature and compare the UI chain to the signed chainId.
- Invariant to test: The chain displayed and the chain signed must be identical for every signature.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: interleave a switch with a signature through deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes) and assert consistency.
