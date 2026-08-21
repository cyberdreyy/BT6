# Q1610: access token captured in the signing closure in sign-wallet-request.ts

## Question
The signer closure captures the access token at construction; can an attacker keep a stale closure alive so SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken) signs using a token belonging to a previous session?

## Target
- File/function: [src/wallet-api/sign-wallet-request.ts](src/wallet-api/sign-wallet-request.ts) - SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken)
- Entrypoint: every wallet-api signature
- Attacker controls: which message string is handed to the user signer and what it commits to
- Exploit idea: Obtain the closure, change sessions, then sign.
- Invariant to test: Signing must resolve the current session token at call time.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: change sessions and assert SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken) refuses to reuse the captured token.
