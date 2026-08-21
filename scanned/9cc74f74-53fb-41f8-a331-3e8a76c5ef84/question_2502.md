# Q2502: typed data mutated before sending in index.ts

## Question
crossApp signTypedData passes the typed data through generateDomainType, which rewrites the EIP712Domain entry; can an attacker use crossApp action barrel: loginWithCrossAppAuth so the provider signs typed data whose type list differs from what the app displayed?

## Target
- File/function: [src/action/crossApp/index.ts](src/action/crossApp/index.ts) - crossApp action barrel: loginWithCrossAppAuth, linkWithCrossAppAuth, wallet.{signMessage,signTypedData,sendTransaction}
- Entrypoint: privy.crossApp.*
- Attacker controls: which dependency object (client, openAuthSession) is bound to each action
- Exploit idea: Submit typed data with an explicit EIP712Domain and compare before/after.
- Invariant to test: The bytes sent for signature must equal the bytes shown to the user.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: diff input and outbound typed data in crossApp action barrel: loginWithCrossAppAuth and assert equality.
