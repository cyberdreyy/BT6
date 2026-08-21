# Q2156: raw-sign hashes anything in create.ts

## Question
rawSign forwards the caller's params to WalletRawSign under the same signed envelope; can an attacker use create(): WalletCreate with optional privy-idempotency-key header to obtain a raw signature over a transaction digest that the wallet would never sign through a typed path?

## Target
- File/function: [src/wallet-api/create.ts](src/wallet-api/create.ts) - create(): WalletCreate with optional privy-idempotency-key header, owner_id: undefined
- Entrypoint: privy.embeddedWallet.create in server-wallet mode
- Attacker controls: chain_type, idempotency key, repetition/concurrency
- Exploit idea: Submit a transaction hash through the raw-sign entrypoint.
- Invariant to test: Raw-hash signing must require an explicit, distinct user approval.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: submit a transaction digest through create(): WalletCreate with optional privy-idempotency-key header and assert an approval gate is enforced.
