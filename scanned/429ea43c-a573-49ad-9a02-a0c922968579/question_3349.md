# Q3349: base64 and utf8 conversions lose bytes in resolve.ts

## Question
The encoding helpers convert signing payloads through utf8 and base64; can an attacker submit bytes that are not valid UTF-8 so resolveCrypto: digest and randomUUID defaults from globalThis.crypto signs a lossy re-encoding of the intended payload?

## Target
- File/function: [src/crypto/resolve.ts](src/crypto/resolve.ts) - resolveCrypto: digest and randomUUID defaults from globalThis.crypto
- Entrypoint: new Privy({crypto})
- Attacker controls: the crypto object the integrator/app passes in, and fallback behaviour when subtle is unavailable
- Exploit idea: Pass a payload with lone surrogates or 0xFF bytes and compare round-tripped output.
- Invariant to test: Encoding round trips must be byte-exact for anything that gets signed.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: round-trip non-UTF-8 byte sequences through resolveCrypto: digest and randomUUID defaults from globalThis.crypto and assert byte equality.
