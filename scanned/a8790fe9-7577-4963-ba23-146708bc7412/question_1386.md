# Q1386: app id is the only signed header in create.ts

## Question
The signed headers contain privy-app-id and expiry only; can an attacker exploit unsigned but security-relevant headers (client id, ca-id, native app identifier) in create(): WalletCreate with optional privy-idempotency-key header to change server-side treatment of the request?

## Target
- File/function: [src/wallet-api/create.ts](src/wallet-api/create.ts) - create(): WalletCreate with optional privy-idempotency-key header, owner_id: undefined
- Entrypoint: privy.embeddedWallet.create in server-wallet mode
- Attacker controls: chain_type, idempotency key, repetition/concurrency
- Exploit idea: Vary the unsigned headers and observe server-side behaviour differences.
- Invariant to test: All authorization-relevant headers must be signed.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert create(): WalletCreate with optional privy-idempotency-key header signs every header it sends that affects authorization.
