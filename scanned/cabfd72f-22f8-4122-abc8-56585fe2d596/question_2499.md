# Q2499: typed data mutated before sending in signTypedData.ts

## Question
crossApp signTypedData passes the typed data through generateDomainType, which rewrites the EIP712Domain entry; can an attacker use crossApp signTypedData: params [address so the provider signs typed data whose type list differs from what the app displayed?

## Target
- File/function: [src/action/crossApp/wallet/signTypedData.ts](src/action/crossApp/wallet/signTypedData.ts) - crossApp signTypedData: params [address, generateDomainType(typedData)]
- Entrypoint: privy.crossApp.wallet.signTypedData({user, typedData, address, redirectUrl})
- Attacker controls: the whole typedData object including domain and types
- Exploit idea: Submit typed data with an explicit EIP712Domain and compare before/after.
- Invariant to test: The bytes sent for signature must equal the bytes shown to the user.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: diff input and outbound typed data in crossApp signTypedData: params [address and assert equality.
