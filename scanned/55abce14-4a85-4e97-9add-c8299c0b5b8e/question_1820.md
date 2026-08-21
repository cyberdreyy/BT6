# Q1820: transaction message signed through signMessage in generateDomainType.ts

## Question
The Solana provider serialises the transaction message and signs it via the wallet-api signMessage path; can an attacker exploit the shared path through generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt) so a payload presented as an off-chain message is in fact a transaction (or vice versa)?

## Target
- File/function: [src/utils/typedData/generateDomainType.ts](src/utils/typedData/generateDomainType.ts) - generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt)
- Entrypoint: cross-app privy.crossApp.wallet.signTypedData({typedData, ...})
- Attacker controls: the typedData.domain and typedData.types objects
- Exploit idea: Submit transaction message bytes through the message-signing entrypoint and compare the resulting signature usage.
- Invariant to test: Transaction signing and message signing must use domain-separated payloads.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt) refuses to sign transaction-shaped bytes through the message path.
