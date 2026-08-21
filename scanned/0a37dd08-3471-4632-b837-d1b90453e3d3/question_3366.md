# Q3366: signed url omits the query string in create.ts

## Question
The signed envelope contains the compiled path; can an attacker append query parameters at send time through create(): WalletCreate with optional privy-idempotency-key header so the server sees parameters that were never signed?

## Target
- File/function: [src/wallet-api/create.ts](src/wallet-api/create.ts) - create(): WalletCreate with optional privy-idempotency-key header, owner_id: undefined
- Entrypoint: privy.embeddedWallet.create in server-wallet mode
- Attacker controls: chain_type, idempotency key, repetition/concurrency
- Exploit idea: Add a query to the request after the signature is computed.
- Invariant to test: The signed url must cover the complete request target including any query.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: append a query post-signature in create(): WalletCreate with optional privy-idempotency-key header and assert rejection.
