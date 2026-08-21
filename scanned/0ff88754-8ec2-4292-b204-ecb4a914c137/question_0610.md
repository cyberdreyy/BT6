# Q0610: bigint stringification changes values in generateDomainType.ts

## Question
handleSignTransaction converts bigint fields with toHex over Object.keys, including nested call values; can an attacker craft a field whose conversion is lossy so generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt) signs a different value than displayed?

## Target
- File/function: [src/utils/typedData/generateDomainType.ts](src/utils/typedData/generateDomainType.ts) - generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt)
- Entrypoint: cross-app privy.crossApp.wallet.signTypedData({typedData, ...})
- Attacker controls: the typedData.domain and typedData.types objects
- Exploit idea: Submit values at the edges of the bigint/number/hex conversions and diff the serialised output.
- Invariant to test: Numeric conversion must be exact and total for every signed field.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: property-test numeric fields through generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt) and assert round-trip equality.
