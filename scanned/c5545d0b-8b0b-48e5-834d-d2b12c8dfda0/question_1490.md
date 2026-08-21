# Q1490: typed data domain unchecked against the chain in generateDomainType.ts

## Question
The domain (chainId, verifyingContract) is forwarded verbatim; can an attacker sign typed data whose domain chainId differs from the provider chain via generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt), producing a signature valid on another chain?

## Target
- File/function: [src/utils/typedData/generateDomainType.ts](src/utils/typedData/generateDomainType.ts) - generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt)
- Entrypoint: cross-app privy.crossApp.wallet.signTypedData({typedData, ...})
- Attacker controls: the typedData.domain and typedData.types objects
- Exploit idea: Submit typed data with a foreign domain.chainId while the provider is on mainnet.
- Invariant to test: The typed-data domain must agree with the provider's active chain.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: submit a mismatched domain chainId to generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt) and assert rejection.
