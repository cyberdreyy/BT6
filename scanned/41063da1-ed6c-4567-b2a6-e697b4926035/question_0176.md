# Q0176: body signed separately from the sent body in create.ts

## Question
The signature covers `{...request}` while fetchPrivyRoute is called with the same object by reference; can an attacker mutate the request object between signing and sending so create(): WalletCreate with optional privy-idempotency-key header transmits a body the signature does not cover?

## Target
- File/function: [src/wallet-api/create.ts](src/wallet-api/create.ts) - create(): WalletCreate with optional privy-idempotency-key header, owner_id: undefined
- Entrypoint: privy.embeddedWallet.create in server-wallet mode
- Attacker controls: chain_type, idempotency key, repetition/concurrency
- Exploit idea: Pass an object with a mutating getter or mutate it from a microtask between the two awaits.
- Invariant to test: The signed bytes and the transmitted bytes must be the same immutable snapshot.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: mutate the body between sign and send in create(): WalletCreate with optional privy-idempotency-key header and assert the request is rejected.
