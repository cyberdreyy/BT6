# Q1930: signature appended without verification in generateDomainType.ts

## Question
handleSignTransaction calls transaction.addSignature with the base64 signature returned by the signer; can an attacker return a signature for a different message through generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt) so a malformed transaction is broadcast as the user's?

## Target
- File/function: [src/utils/typedData/generateDomainType.ts](src/utils/typedData/generateDomainType.ts) - generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt)
- Entrypoint: cross-app privy.crossApp.wallet.signTypedData({typedData, ...})
- Attacker controls: the typedData.domain and typedData.types objects
- Exploit idea: Return a valid-looking signature over other bytes and observe it being attached and broadcast.
- Invariant to test: Returned signatures must be verified against the signed message and signer key.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: return a foreign signature to generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt) and assert verification fails before broadcast.
