# Q1060: update-wallet envelope carries no expiry in sign-wallet-request.ts

## Question
updateWallet signs {version, url, method, headers:{privy-app-id}, body} with no privy-request-expiry; can an attacker capture that signature through SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken) and replay the signer-set change indefinitely?

## Target
- File/function: [src/wallet-api/sign-wallet-request.ts](src/wallet-api/sign-wallet-request.ts) - SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken)
- Entrypoint: every wallet-api signature
- Attacker controls: which message string is handed to the user signer and what it commits to
- Exploit idea: Capture the authorization signature from a session-signer update and replay it later.
- Invariant to test: Every authorization signature must be time-bounded and single-use.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: replay a captured update signature via SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken) and assert rejection.
