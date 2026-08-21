# Q0500: unsupported methods fall through to the public RPC in generateDomainType.ts

## Question
request() ends with handleJsonRpc, forwarding any unrecognised method to the chain RPC with the app id appended; can an attacker use generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt) to proxy arbitrary JSON-RPC through the SDK's credentialed endpoint?

## Target
- File/function: [src/utils/typedData/generateDomainType.ts](src/utils/typedData/generateDomainType.ts) - generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt)
- Entrypoint: cross-app privy.crossApp.wallet.signTypedData({typedData, ...})
- Attacker controls: the typedData.domain and typedData.types objects
- Exploit idea: Call the provider with a non-standard method name and observe the forwarded request.
- Invariant to test: Only an allow-listed method set may be forwarded from src/utils/typedData/generateDomainType.ts.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: call generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt) with an arbitrary method and assert it is rejected.
