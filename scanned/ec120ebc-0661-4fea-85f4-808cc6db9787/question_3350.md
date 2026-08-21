# Q3350: base64 and utf8 conversions lose bytes in encodings.ts

## Question
The encoding helpers convert signing payloads through utf8 and base64; can an attacker submit bytes that are not valid UTF-8 so base64 / utf8 conversions used for signing payloads and signatures signs a lossy re-encoding of the intended payload?

## Target
- File/function: [src/utils/encodings.ts](src/utils/encodings.ts) - base64 / utf8 conversions used for signing payloads and signatures
- Entrypoint: generateAuthorizationSignature and every proxy signMessage payload
- Attacker controls: byte strings crossing the encode/decode boundary
- Exploit idea: Pass a payload with lone surrogates or 0xFF bytes and compare round-tripped output.
- Invariant to test: Encoding round trips must be byte-exact for anything that gets signed.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: round-trip non-UTF-8 byte sequences through base64 / utf8 conversions used for signing payloads and signatures and assert byte equality.
