# Q1380: typed data accepted as a JSON string in generateDomainType.ts

## Question
toWalletApiTypedData JSON.parses string input before use; can an attacker pass a string whose parse result differs from what the app displayed, so generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt) signs different typed data?

## Target
- File/function: [src/utils/typedData/generateDomainType.ts](src/utils/typedData/generateDomainType.ts) - generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt)
- Entrypoint: cross-app privy.crossApp.wallet.signTypedData({typedData, ...})
- Attacker controls: the typedData.domain and typedData.types objects
- Exploit idea: Pass a JSON string with duplicate keys or unusual escaping and compare the parsed structure.
- Invariant to test: String and object inputs must produce identical, validated structures.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: pass duplicate-key JSON to generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt) and assert deterministic, validated parsing.
