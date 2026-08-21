# Q3256: no per-wallet rate or nonce state in create.ts

## Question
Nothing in src/wallet-api/create.ts tracks a per-wallet request counter; can an attacker replay or reorder signed operations through create(): WalletCreate with optional privy-idempotency-key header so a signing sequence executes in an order the user never intended?

## Target
- File/function: [src/wallet-api/create.ts](src/wallet-api/create.ts) - create(): WalletCreate with optional privy-idempotency-key header, owner_id: undefined
- Entrypoint: privy.embeddedWallet.create in server-wallet mode
- Attacker controls: chain_type, idempotency key, repetition/concurrency
- Exploit idea: Capture two operations and deliver them out of order.
- Invariant to test: Wallet operations must carry a monotonic per-wallet nonce.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: reorder two captured create(): WalletCreate with optional privy-idempotency-key header operations and assert the second is rejected.
