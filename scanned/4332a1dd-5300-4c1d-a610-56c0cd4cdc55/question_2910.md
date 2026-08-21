# Q2910: hex detection via loose regex in encodings.ts

## Question
The hex predicate accepts any 0x-prefixed hex string of any length, including empty; can an attacker exploit that in base64 / utf8 conversions used for signing payloads and signatures so a zero-length or odd-length value is passed to the signer?

## Target
- File/function: [src/utils/encodings.ts](src/utils/encodings.ts) - base64 / utf8 conversions used for signing payloads and signatures
- Entrypoint: generateAuthorizationSignature and every proxy signMessage payload
- Attacker controls: byte strings crossing the encode/decode boundary
- Exploit idea: Submit '0x' and an odd-length hex string.
- Invariant to test: Hex inputs must be length-validated before signing.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: feed '0x' and odd-length values to base64 / utf8 conversions used for signing payloads and signatures and assert rejection.
