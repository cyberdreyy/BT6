# Q0820: bigint and undefined fields collapse the cache key in encodings.ts

## Question
The cache key is built with JSON.stringify, which drops undefined values and functions; can an attacker craft two different payloads that produce the same key inside base64 / utf8 conversions used for signing payloads and signatures?

## Target
- File/function: [src/utils/encodings.ts](src/utils/encodings.ts) - base64 / utf8 conversions used for signing payloads and signatures
- Entrypoint: generateAuthorizationSignature and every proxy signMessage payload
- Attacker controls: byte strings crossing the encode/decode boundary
- Exploit idea: Pass payloads differing only by an undefined field and observe the shared cache entry.
- Invariant to test: Cache keys must be injective over the payloads they represent.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert base64 / utf8 conversions used for signing payloads and signatures produces different keys for payloads differing only in undefined-valued fields.
