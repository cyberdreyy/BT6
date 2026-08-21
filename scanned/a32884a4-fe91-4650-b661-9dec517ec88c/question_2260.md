# Q2260: versioned detection by a property name in generateDomainType.ts

## Question
isVersionedTransaction only checks for a 'version' property; can an attacker pass an object carrying that property so generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt) takes the versioned branch on a legacy transaction and serialises the wrong bytes?

## Target
- File/function: [src/utils/typedData/generateDomainType.ts](src/utils/typedData/generateDomainType.ts) - generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt)
- Entrypoint: cross-app privy.crossApp.wallet.signTypedData({typedData, ...})
- Attacker controls: the typedData.domain and typedData.types objects
- Exploit idea: Pass a legacy transaction object with an added version field.
- Invariant to test: Transaction kind detection must use structural validation.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: pass a spoofed object to generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt) and assert detection is structural.
