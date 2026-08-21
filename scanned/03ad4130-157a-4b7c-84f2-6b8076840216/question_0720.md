# Q0720: quantity parser rejects only some shapes in generateDomainType.ts

## Question
toQuantity accepts numbers, bigints and 0x-hex but throws otherwise; can an attacker pass a value that survives the check yet decodes differently server-side through generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt)?

## Target
- File/function: [src/utils/typedData/generateDomainType.ts](src/utils/typedData/generateDomainType.ts) - generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt)
- Entrypoint: cross-app privy.crossApp.wallet.signTypedData({typedData, ...})
- Attacker controls: the typedData.domain and typedData.types objects
- Exploit idea: Feed '0x0000...01', leading-zero hex and oversized values.
- Invariant to test: Quantity encoding must be canonical for every signed field.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: feed a canonicalisation table to generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt) and assert a single normalised output.
