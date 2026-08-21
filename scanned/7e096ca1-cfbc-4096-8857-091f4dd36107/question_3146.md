# Q3146: json body serialised twice in create.ts

## Question
PrivyInternal.fetch JSON.stringifies the body while the signature covers the pre-serialisation object; can an attacker exploit serialisation differences (key order, unicode escaping, number formatting) so create(): WalletCreate with optional privy-idempotency-key header signs one byte string and sends another?

## Target
- File/function: [src/wallet-api/create.ts](src/wallet-api/create.ts) - create(): WalletCreate with optional privy-idempotency-key header, owner_id: undefined
- Entrypoint: privy.embeddedWallet.create in server-wallet mode
- Attacker controls: chain_type, idempotency key, repetition/concurrency
- Exploit idea: Include unicode, large numbers and key orders that differ between canonicalize and JSON.stringify.
- Invariant to test: Signed and transmitted encodings must be byte-identical.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert canonicalize output and the transmitted body are byte-equal for create(): WalletCreate with optional privy-idempotency-key header.
