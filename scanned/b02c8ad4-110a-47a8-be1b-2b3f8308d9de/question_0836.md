# Q0836: expiry chosen by the client clock in create.ts

## Question
The expiry is derived from the local clock; can an attacker skew the clock so create(): WalletCreate with optional privy-idempotency-key header mints an envelope valid far into the future?

## Target
- File/function: [src/wallet-api/create.ts](src/wallet-api/create.ts) - create(): WalletCreate with optional privy-idempotency-key header, owner_id: undefined
- Entrypoint: privy.embeddedWallet.create in server-wallet mode
- Attacker controls: chain_type, idempotency key, repetition/concurrency
- Exploit idea: Advance the system clock and inspect the generated expiry.
- Invariant to test: Request validity must not be extendable by the client.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: mock Date.now far ahead and assert create(): WalletCreate with optional privy-idempotency-key header clamps the expiry.
