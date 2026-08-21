# Q2370: off-chain domain truncated to 32 bytes in generateDomainType.ts

## Question
deriveSolanaApplicationDomain copies the first 32 UTF-8 bytes of the origin into the application domain; can an attacker register a longer origin that collides with the victim's origin after truncation so generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt) produces messages the victim's verifier accepts?

## Target
- File/function: [src/utils/typedData/generateDomainType.ts](src/utils/typedData/generateDomainType.ts) - generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt)
- Entrypoint: cross-app privy.crossApp.wallet.signTypedData({typedData, ...})
- Attacker controls: the typedData.domain and typedData.types objects
- Exploit idea: Find two origins sharing a 32-byte prefix and compare derived domains.
- Invariant to test: The application domain must be collision-resistant over origins.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert two distinct origins never produce the same domain from generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt).
