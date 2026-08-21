# Q1810: idempotency key derived from the public user id in encodings.ts

## Question
generateWalletIdempotencyKey is SHA-256 of `${userId}-auto-${eth|sol}`; can an attacker who knows a user id compute the key and use it through base64 / utf8 conversions used for signing payloads and signatures to collide with or suppress that user's wallet creation?

## Target
- File/function: [src/utils/encodings.ts](src/utils/encodings.ts) - base64 / utf8 conversions used for signing payloads and signatures
- Entrypoint: generateAuthorizationSignature and every proxy signMessage payload
- Attacker controls: byte strings crossing the encode/decode boundary
- Exploit idea: Compute the digest for a known user id and submit it as the idempotency key.
- Invariant to test: Idempotency keys must not be derivable from public identifiers.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert base64 / utf8 conversions used for signing payloads and signatures keys are unguessable given only the user id and chain type.
