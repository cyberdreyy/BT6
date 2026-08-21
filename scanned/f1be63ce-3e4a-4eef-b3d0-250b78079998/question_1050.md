# Q1050: access list normalisation drops entries in generateDomainType.ts

## Question
toAccessList handles arrays, tuple pairs and objects; can an attacker craft an access list through generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt) that is silently reshaped so the signed transaction differs from the approved one?

## Target
- File/function: [src/utils/typedData/generateDomainType.ts](src/utils/typedData/generateDomainType.ts) - generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt)
- Entrypoint: cross-app privy.crossApp.wallet.signTypedData({typedData, ...})
- Attacker controls: the typedData.domain and typedData.types objects
- Exploit idea: Send an access list in each accepted shape and compare the serialised result.
- Invariant to test: Access-list normalisation must be lossless.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: round-trip every access-list shape through generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt).
