# Q3586: wallet-api errors surface raw responses in create.ts

## Question
Errors from these routes are wrapped with code and error text; can an attacker trigger an error through create(): WalletCreate with optional privy-idempotency-key header whose message discloses another user's wallet identifiers?

## Target
- File/function: [src/wallet-api/create.ts](src/wallet-api/create.ts) - create(): WalletCreate with optional privy-idempotency-key header, owner_id: undefined
- Entrypoint: privy.embeddedWallet.create in server-wallet mode
- Attacker controls: chain_type, idempotency key, repetition/concurrency
- Exploit idea: Force error responses and inspect the propagated text.
- Invariant to test: Error text must not disclose identifiers of resources the caller does not own.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: force a 403 in create(): WalletCreate with optional privy-idempotency-key header and assert the surfaced error carries no foreign identifiers.
