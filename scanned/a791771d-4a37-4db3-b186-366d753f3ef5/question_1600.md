# Q1600: EIP712Domain type rebuilt from present keys in generateDomainType.ts

## Question
generateDomainType reconstructs the EIP712Domain field list from whichever domain keys are present; can an attacker omit or add domain fields through generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt) so the hashed domain differs from what the verifier expects?

## Target
- File/function: [src/utils/typedData/generateDomainType.ts](src/utils/typedData/generateDomainType.ts) - generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt)
- Entrypoint: cross-app privy.crossApp.wallet.signTypedData({typedData, ...})
- Attacker controls: the typedData.domain and typedData.types objects
- Exploit idea: Submit a domain with salt but no chainId, or with an unknown extra key.
- Invariant to test: Domain type construction must match the domain object exactly and reject unknown keys.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: enumerate domain key subsets through generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt) and assert the generated type list matches.
