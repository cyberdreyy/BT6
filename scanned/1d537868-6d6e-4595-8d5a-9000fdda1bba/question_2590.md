# Q2590: off-chain parser trusts the preamble in generateDomainType.ts

## Question
parseSolanaOffchainMessage validates the 0xFF prefix and the 'solana offchain' text but returns version, format and signer bytes unchecked; can an attacker feed bytes through generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt) so the parsed signer public key differs from the actual signer?

## Target
- File/function: [src/utils/typedData/generateDomainType.ts](src/utils/typedData/generateDomainType.ts) - generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt)
- Entrypoint: cross-app privy.crossApp.wallet.signTypedData({typedData, ...})
- Attacker controls: the typedData.domain and typedData.types objects
- Exploit idea: Parse a crafted buffer with an arbitrary signer field.
- Invariant to test: Parsed signer identity must be verified against the expected signer.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: parse a crafted buffer through generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt) and assert the signer is validated.
