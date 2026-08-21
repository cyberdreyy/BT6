# Q3690: chain id switch emits an event apps trust in generateDomainType.ts

## Question
internalSwitchEthereumChain emits chainChanged after mutating internal state; can an attacker force a switch through generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt) so the app's UI shows one chain while signing occurs on another?

## Target
- File/function: [src/utils/typedData/generateDomainType.ts](src/utils/typedData/generateDomainType.ts) - generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt)
- Entrypoint: cross-app privy.crossApp.wallet.signTypedData({typedData, ...})
- Attacker controls: the typedData.domain and typedData.types objects
- Exploit idea: Trigger a switch during a pending signature and compare the UI chain to the signed chainId.
- Invariant to test: The chain displayed and the chain signed must be identical for every signature.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: interleave a switch with a signature through generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt) and assert consistency.
