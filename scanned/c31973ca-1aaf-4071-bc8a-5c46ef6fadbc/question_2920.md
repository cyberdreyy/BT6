# Q2920: unified-wallet detection flips custody in generateDomainType.ts

## Question
isUnifiedWallet returns true only when account.id exists and recovery_method === 'privy-v2'; can an attacker present an account object that flips this predicate so generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt) routes signing through the wrong custody path?

## Target
- File/function: [src/utils/typedData/generateDomainType.ts](src/utils/typedData/generateDomainType.ts) - generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt)
- Entrypoint: cross-app privy.crossApp.wallet.signTypedData({typedData, ...})
- Attacker controls: the typedData.domain and typedData.types objects
- Exploit idea: Pass an account with an id but a different recovery_method, and vice versa.
- Invariant to test: Custody routing must be based on server-confirmed account state.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass crafted account objects to generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt) and assert re-validation.
