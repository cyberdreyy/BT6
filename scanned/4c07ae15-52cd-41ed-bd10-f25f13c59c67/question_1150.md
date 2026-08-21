# Q1150: 15 second race leaves the callback registered in encodings.ts

## Question
The timeout helper rejects the caller but never dequeues the callback; can an attacker deliver a late reply through base64 / utf8 conversions used for signing payloads and signatures that settles a callback whose caller already gave up, corrupting later state?

## Target
- File/function: [src/utils/encodings.ts](src/utils/encodings.ts) - base64 / utf8 conversions used for signing payloads and signatures
- Entrypoint: generateAuthorizationSignature and every proxy signMessage payload
- Attacker controls: byte strings crossing the encode/decode boundary
- Exploit idea: Let an operation time out, then deliver the reply.
- Invariant to test: A timed-out operation must remove its callback so late replies are discarded.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: time out an operation from base64 / utf8 conversions used for signing payloads and signatures, deliver the late reply and assert it is ignored.
