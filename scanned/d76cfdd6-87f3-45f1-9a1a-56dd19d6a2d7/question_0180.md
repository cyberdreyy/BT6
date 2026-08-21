# Q0180: body signed separately from the sent body in sign-wallet-request.ts

## Question
The signature covers `{...request}` while fetchPrivyRoute is called with the same object by reference; can an attacker mutate the request object between signing and sending so SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken) transmits a body the signature does not cover?

## Target
- File/function: [src/wallet-api/sign-wallet-request.ts](src/wallet-api/sign-wallet-request.ts) - SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken)
- Entrypoint: every wallet-api signature
- Attacker controls: which message string is handed to the user signer and what it commits to
- Exploit idea: Pass an object with a mutating getter or mutate it from a microtask between the two awaits.
- Invariant to test: The signed bytes and the transmitted bytes must be the same immutable snapshot.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: mutate the body between sign and send in SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken) and assert the request is rejected.
