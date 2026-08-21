# Q2497: typed data mutated before sending in throwIfNotLoggedIn.ts

## Question
crossApp signTypedData passes the typed data through generateDomainType, which rewrites the EIP712Domain entry; can an attacker use throwIfNotLoggedIn(user): only checks the user object passed by the caller so the provider signs typed data whose type list differs from what the app displayed?

## Target
- File/function: [src/action/crossApp/wallet/utils/throwIfNotLoggedIn.ts](src/action/crossApp/wallet/utils/throwIfNotLoggedIn.ts) - throwIfNotLoggedIn(user): only checks the user object passed by the caller
- Entrypoint: every crossApp.wallet action
- Attacker controls: the user object supplied by the caller rather than read from session
- Exploit idea: Submit typed data with an explicit EIP712Domain and compare before/after.
- Invariant to test: The bytes sent for signature must equal the bytes shown to the user.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: diff input and outbound typed data in throwIfNotLoggedIn(user): only checks the user object passed by the caller and assert equality.
