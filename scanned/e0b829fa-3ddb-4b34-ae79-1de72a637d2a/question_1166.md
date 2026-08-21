# Q1166: version field is a constant in create.ts

## Question
Every envelope sets version: 1; can an attacker exploit the absence of a nonce or request id so two identical operations from create(): WalletCreate with optional privy-idempotency-key header produce byte-identical signatures that are interchangeable?

## Target
- File/function: [src/wallet-api/create.ts](src/wallet-api/create.ts) - create(): WalletCreate with optional privy-idempotency-key header, owner_id: undefined
- Entrypoint: privy.embeddedWallet.create in server-wallet mode
- Attacker controls: chain_type, idempotency key, repetition/concurrency
- Exploit idea: Issue the same operation twice and compare signatures.
- Invariant to test: Envelopes must include a unique per-request nonce.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: issue the same create(): WalletCreate with optional privy-idempotency-key header operation twice and assert the signatures differ.
