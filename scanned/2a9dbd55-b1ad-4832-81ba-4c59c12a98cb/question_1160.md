# Q1160: fee payer signature parity inference in generateDomainType.ts

## Question
toFeePayerSignature derives yParity from v-27 when yParity is absent; can an attacker supply a v value that yields a wrong parity accepted by generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt)?

## Target
- File/function: [src/utils/typedData/generateDomainType.ts](src/utils/typedData/generateDomainType.ts) - generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt)
- Entrypoint: cross-app privy.crossApp.wallet.signTypedData({typedData, ...})
- Attacker controls: the typedData.domain and typedData.types objects
- Exploit idea: Send v values such as 0, 1, 35 and 36 and inspect the derived parity.
- Invariant to test: Signature parity must be derived unambiguously or rejected.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: table-test v/yParity inputs through generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt).
