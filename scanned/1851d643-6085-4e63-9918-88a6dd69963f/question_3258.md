# Q3258: no per-wallet rate or nonce state in update-wallet.ts

## Question
Nothing in src/wallet-api/update-wallet.ts tracks a per-wallet request counter; can an attacker replay or reorder signed operations through updateWallet(): signs {version:1 so a signing sequence executes in an order the user never intended?

## Target
- File/function: [src/wallet-api/update-wallet.ts](src/wallet-api/update-wallet.ts) - updateWallet(): signs {version:1, url, method, headers:{privy-app-id}, body} with NO privy-request-expiry header
- Entrypoint: session signer add/remove
- Attacker controls: the body (additional_signers) and the resulting long-lived authorization signature
- Exploit idea: Capture two operations and deliver them out of order.
- Invariant to test: Wallet operations must carry a monotonic per-wallet nonce.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: reorder two captured updateWallet(): signs {version:1 operations and assert the second is rejected.
