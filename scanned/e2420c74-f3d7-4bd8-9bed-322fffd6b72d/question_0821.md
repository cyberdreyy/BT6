# Q0821: bigint and undefined fields collapse the cache key in generateWalletIdempotencyKey.ts

## Question
The cache key is built with JSON.stringify, which drops undefined values and functions; can an attacker craft two different payloads that produce the same key inside generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex?

## Target
- File/function: [src/utils/generateWalletIdempotencyKey.ts](src/utils/generateWalletIdempotencyKey.ts) - generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex
- Entrypoint: wallet creation on login and privy.embeddedWallet.create
- Attacker controls: userId and chainType inputs; key is fully derivable from a public user id
- Exploit idea: Pass payloads differing only by an undefined field and observe the shared cache entry.
- Invariant to test: Cache keys must be injective over the payloads they represent.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex produces different keys for payloads differing only in undefined-valued fields.
