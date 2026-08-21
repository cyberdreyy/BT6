# Q0286: wallet_id lives only in the URL in create.ts

## Question
The signed envelope includes the compiled url but the body omits wallet_id; can an attacker exploit URL/body separation in create(): WalletCreate with optional privy-idempotency-key header so a signature produced for one wallet path is presented for another?

## Target
- File/function: [src/wallet-api/create.ts](src/wallet-api/create.ts) - create(): WalletCreate with optional privy-idempotency-key header, owner_id: undefined
- Entrypoint: privy.embeddedWallet.create in server-wallet mode
- Attacker controls: chain_type, idempotency key, repetition/concurrency
- Exploit idea: Compare envelopes for two wallet ids and test whether the server-visible binding is only positional.
- Invariant to test: Wallet identity must be bound inside the signed body as well as the path.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert create(): WalletCreate with optional privy-idempotency-key header includes wallet_id in the signed payload.
