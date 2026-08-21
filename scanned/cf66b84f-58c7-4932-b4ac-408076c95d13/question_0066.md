# Q0066: unsigned headers appended after signing in create.ts

## Question
rpc() signs an envelope containing only privy-app-id and privy-request-expiry, then spreads the caller's extraHeaders after the signature header; can an unprivileged attacker pass headers through privy.embeddedWallet.create in server-wallet mode that are transmitted but not covered by the authorization signature, or that overwrite the signature header itself?

## Target
- File/function: [src/wallet-api/create.ts](src/wallet-api/create.ts) - create(): WalletCreate with optional privy-idempotency-key header, owner_id: undefined
- Entrypoint: privy.embeddedWallet.create in server-wallet mode
- Attacker controls: chain_type, idempotency key, repetition/concurrency
- Exploit idea: Call the wallet RPC path with an extraHeaders object containing privy-authorization-signature and privy-request-expiry and inspect the outgoing request.
- Invariant to test: Every header that influences server-side authorization must be inside the signed envelope and immutable afterwards.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call create(): WalletCreate with optional privy-idempotency-key header with crafted extraHeaders and assert the final headers equal the signed set.
