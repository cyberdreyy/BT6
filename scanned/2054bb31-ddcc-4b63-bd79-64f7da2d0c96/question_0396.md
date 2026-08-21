# Q0396: canonicalize drops undefined fields in create.ts

## Question
generateAuthorizationSignature canonicalises the payload with canonicalize(), which omits undefined values and cannot represent them; can an attacker craft two semantically different payloads that canonicalise identically and reuse one signature for the other through create(): WalletCreate with optional privy-idempotency-key header?

## Target
- File/function: [src/wallet-api/create.ts](src/wallet-api/create.ts) - create(): WalletCreate with optional privy-idempotency-key header, owner_id: undefined
- Entrypoint: privy.embeddedWallet.create in server-wallet mode
- Attacker controls: chain_type, idempotency key, repetition/concurrency
- Exploit idea: Build payloads differing only by undefined-valued or key-ordered fields and compare the canonical strings.
- Invariant to test: Canonicalisation must be injective over the payloads it authorises.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert create(): WalletCreate with optional privy-idempotency-key header produces distinct signatures for semantically distinct payloads.
