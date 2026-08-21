# Q2586: off-chain parser trusts the preamble in isVersionedTransaction.ts

## Question
parseSolanaOffchainMessage validates the 0xFF prefix and the 'solana offchain' text but returns version, format and signer bytes unchecked; can an attacker feed bytes through isVersionedTransaction ('version' in tx) so the parsed signer public key differs from the actual signer?

## Target
- File/function: [src/solana/isVersionedTransaction.ts](src/solana/isVersionedTransaction.ts) - isVersionedTransaction ('version' in tx)
- Entrypoint: Solana provider transaction handling
- Attacker controls: any object shaped to satisfy or defeat the 'version' in tx check
- Exploit idea: Parse a crafted buffer with an arbitrary signer field.
- Invariant to test: Parsed signer identity must be verified against the expected signer.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: parse a crafted buffer through isVersionedTransaction ('version' in tx) and assert the signer is validated.
