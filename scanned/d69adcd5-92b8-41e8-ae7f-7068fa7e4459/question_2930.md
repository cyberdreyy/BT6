# Q2930: signature not bound to the access token in sign-wallet-request.ts

## Question
The envelope commits to app id and expiry but not to the session token used to authenticate; can an attacker present a signature from SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken) together with a different session token?

## Target
- File/function: [src/wallet-api/sign-wallet-request.ts](src/wallet-api/sign-wallet-request.ts) - SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken)
- Entrypoint: every wallet-api signature
- Attacker controls: which message string is handed to the user signer and what it commits to
- Exploit idea: Pair a captured signature with another token.
- Invariant to test: Authorization signatures must be bound to the session that produced them.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: cross a captured signature with another session and assert rejection.
