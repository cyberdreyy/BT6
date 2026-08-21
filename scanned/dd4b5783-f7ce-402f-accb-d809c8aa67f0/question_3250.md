# Q3250: disconnect leaves the wrapper usable in generateDomainType.ts

## Question
disconnect only calls the standard feature; can an attacker keep using generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt) after disconnect so signatures are still requested from a wallet the user disconnected?

## Target
- File/function: [src/utils/typedData/generateDomainType.ts](src/utils/typedData/generateDomainType.ts) - generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt)
- Entrypoint: cross-app privy.crossApp.wallet.signTypedData({typedData, ...})
- Attacker controls: the typedData.domain and typedData.types objects
- Exploit idea: Call disconnect then sign.
- Invariant to test: A disconnected wallet wrapper must refuse further operations.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call disconnect then sign through generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt) and assert rejection.
