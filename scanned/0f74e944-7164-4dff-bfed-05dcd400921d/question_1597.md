# Q1597: EIP712Domain type rebuilt from present keys in offchain-message.ts

## Question
generateDomainType reconstructs the EIP712Domain field list from whichever domain keys are present; can an attacker omit or add domain fields through deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes) so the hashed domain differs from what the verifier expects?

## Target
- File/function: [src/solana/offchain-message.ts](src/solana/offchain-message.ts) - deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes), buildSolanaOffchainMessage (255 + 'solana offchain' preamble, 1232 max), parseSolanaOffchainMessage
- Entrypoint: off-chain message construction for Solana signing
- Attacker controls: origin/domain string, message contents and length, raw bytes handed to the parser
- Exploit idea: Submit a domain with salt but no chainId, or with an unknown extra key.
- Invariant to test: Domain type construction must match the domain object exactly and reject unknown keys.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: enumerate domain key subsets through deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes) and assert the generated type list matches.
