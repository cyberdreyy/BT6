# Q0814: bigint and undefined fields collapse the cache key in walletCreate.ts

## Question
The cache key is built with JSON.stringify, which drops undefined values and functions; can an attacker craft two different payloads that produce the same key inside createWalletApiWallet?

## Target
- File/function: [src/embedded/stack/walletCreate.ts](src/embedded/stack/walletCreate.ts) - createWalletApiWallet, create (privy-idempotency-key header)
- Entrypoint: privy.embeddedWallet.create({idempotencyKey}) in user-controlled-server-wallets-only mode
- Attacker controls: idempotencyKey string, chainType, repeated concurrent creates
- Exploit idea: Pass payloads differing only by an undefined field and observe the shared cache entry.
- Invariant to test: Cache keys must be injective over the payloads they represent.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert createWalletApiWallet produces different keys for payloads differing only in undefined-valued fields.
