# Q2810: psbt forwarded without inspection in generateDomainType.ts

## Question
signTransaction forwards the psbt argument verbatim to the iframe; can an attacker submit a psbt through generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt) whose outputs differ from what the app displayed?

## Target
- File/function: [src/utils/typedData/generateDomainType.ts](src/utils/typedData/generateDomainType.ts) - generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt)
- Entrypoint: cross-app privy.crossApp.wallet.signTypedData({typedData, ...})
- Attacker controls: the typedData.domain and typedData.types objects
- Exploit idea: Submit a psbt with an added output and observe no client-side checks.
- Invariant to test: The SDK must surface or verify the outputs it asks the user to sign.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt) extracts and exposes psbt outputs for confirmation.
