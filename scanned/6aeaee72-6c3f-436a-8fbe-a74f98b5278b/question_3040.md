# Q3040: failure between sign and send loses atomicity in sign-wallet-request.ts

## Question
If fetchPrivyRoute throws after signing, the signature remains valid; can an attacker force that failure in SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken) and then reuse the signature at a moment of their choosing?

## Target
- File/function: [src/wallet-api/sign-wallet-request.ts](src/wallet-api/sign-wallet-request.ts) - SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken)
- Entrypoint: every wallet-api signature
- Attacker controls: which message string is handed to the user signer and what it commits to
- Exploit idea: Abort the request post-signature and replay it later.
- Invariant to test: An unused authorization signature must be invalidated.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: abort after signing in SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken) and assert the signature cannot be reused.
