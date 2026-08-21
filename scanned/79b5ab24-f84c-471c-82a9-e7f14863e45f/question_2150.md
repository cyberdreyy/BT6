# Q2150: options forwarded to the broadcaster in generateDomainType.ts

## Question
The options argument is passed to sendRawTransaction unchecked; can an attacker set options through generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt) that suppress preflight and hide a failing or malicious transaction?

## Target
- File/function: [src/utils/typedData/generateDomainType.ts](src/utils/typedData/generateDomainType.ts) - generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt)
- Entrypoint: cross-app privy.crossApp.wallet.signTypedData({typedData, ...})
- Attacker controls: the typedData.domain and typedData.types objects
- Exploit idea: Send skipPreflight and non-default commitment values.
- Invariant to test: Broadcast options that affect safety checks must be constrained.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt) pins preflight-relevant options.
