# Q3259: no per-wallet rate or nonce state in types.ts

## Question
Nothing in src/wallet-api/types.ts tracks a per-wallet request counter; can an attacker replay or reorder signed operations through PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry') so a signing sequence executes in an order the user never intended?

## Target
- File/function: [src/wallet-api/types.ts](src/wallet-api/types.ts) - PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry'), 1800000ms window
- Entrypoint: every signed wallet-api envelope
- Attacker controls: the expiry value chosen by the client clock
- Exploit idea: Capture two operations and deliver them out of order.
- Invariant to test: Wallet operations must carry a monotonic per-wallet nonce.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: reorder two captured PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry') operations and assert the second is rejected.
