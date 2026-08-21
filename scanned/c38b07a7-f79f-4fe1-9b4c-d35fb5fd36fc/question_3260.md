# Q3260: no per-wallet rate or nonce state in sign-wallet-request.ts

## Question
Nothing in src/wallet-api/sign-wallet-request.ts tracks a per-wallet request counter; can an attacker replay or reorder signed operations through SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken) so a signing sequence executes in an order the user never intended?

## Target
- File/function: [src/wallet-api/sign-wallet-request.ts](src/wallet-api/sign-wallet-request.ts) - SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken)
- Entrypoint: every wallet-api signature
- Attacker controls: which message string is handed to the user signer and what it commits to
- Exploit idea: Capture two operations and deliver them out of order.
- Invariant to test: Wallet operations must carry a monotonic per-wallet nonce.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: reorder two captured SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken) operations and assert the second is rejected.
