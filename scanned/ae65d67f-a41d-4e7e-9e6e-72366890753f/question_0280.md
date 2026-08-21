# Q0280: populate then sign is not atomic in generateDomainType.ts

## Question
handleSendTransaction populates, then signs, then broadcasts; can an attacker mutate the transaction object between those steps so the user approves one payload and another is signed via generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt)?

## Target
- File/function: [src/utils/typedData/generateDomainType.ts](src/utils/typedData/generateDomainType.ts) - generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt)
- Entrypoint: cross-app privy.crossApp.wallet.signTypedData({typedData, ...})
- Attacker controls: the typedData.domain and typedData.types objects
- Exploit idea: Pass an object with getters that change value between the populate and sign reads.
- Invariant to test: The signed payload must be a frozen snapshot of what was approved.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: pass a self-mutating object to generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt) and assert the signed payload equals the approved snapshot.
