# Q3036: failure between sign and send loses atomicity in create.ts

## Question
If fetchPrivyRoute throws after signing, the signature remains valid; can an attacker force that failure in create(): WalletCreate with optional privy-idempotency-key header and then reuse the signature at a moment of their choosing?

## Target
- File/function: [src/wallet-api/create.ts](src/wallet-api/create.ts) - create(): WalletCreate with optional privy-idempotency-key header, owner_id: undefined
- Entrypoint: privy.embeddedWallet.create in server-wallet mode
- Attacker controls: chain_type, idempotency key, repetition/concurrency
- Exploit idea: Abort the request post-signature and replay it later.
- Invariant to test: An unused authorization signature must be invalidated.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: abort after signing in create(): WalletCreate with optional privy-idempotency-key header and assert the signature cannot be reused.
