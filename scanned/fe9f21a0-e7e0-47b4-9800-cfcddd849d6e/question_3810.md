# Q3810: get-wallet is unsigned in sign-wallet-request.ts

## Question
getWallet issues a plain authenticated GET with no authorization signature; can an attacker use SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken) to enumerate wallet metadata (ids, signers) for wallets reachable with a session token alone?

## Target
- File/function: [src/wallet-api/sign-wallet-request.ts](src/wallet-api/sign-wallet-request.ts) - SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken)
- Entrypoint: every wallet-api signature
- Attacker controls: which message string is handed to the user signer and what it commits to
- Exploit idea: Call the read path for wallet ids not owned by the session.
- Invariant to test: Wallet metadata reads must be scoped to the authenticated owner.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Integration test: read a foreign wallet id through SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken) and assert refusal.
