# Q1809: idempotency key derived from the public user id in resolve.ts

## Question
generateWalletIdempotencyKey is SHA-256 of `${userId}-auto-${eth|sol}`; can an attacker who knows a user id compute the key and use it through resolveCrypto: digest and randomUUID defaults from globalThis.crypto to collide with or suppress that user's wallet creation?

## Target
- File/function: [src/crypto/resolve.ts](src/crypto/resolve.ts) - resolveCrypto: digest and randomUUID defaults from globalThis.crypto
- Entrypoint: new Privy({crypto})
- Attacker controls: the crypto object the integrator/app passes in, and fallback behaviour when subtle is unavailable
- Exploit idea: Compute the digest for a known user id and submit it as the idempotency key.
- Invariant to test: Idempotency keys must not be derivable from public identifiers.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert resolveCrypto: digest and randomUUID defaults from globalThis.crypto keys are unguessable given only the user id and chain type.
