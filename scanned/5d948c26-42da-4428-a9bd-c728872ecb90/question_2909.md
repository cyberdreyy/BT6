# Q2909: hex detection via loose regex in resolve.ts

## Question
The hex predicate accepts any 0x-prefixed hex string of any length, including empty; can an attacker exploit that in resolveCrypto: digest and randomUUID defaults from globalThis.crypto so a zero-length or odd-length value is passed to the signer?

## Target
- File/function: [src/crypto/resolve.ts](src/crypto/resolve.ts) - resolveCrypto: digest and randomUUID defaults from globalThis.crypto
- Entrypoint: new Privy({crypto})
- Attacker controls: the crypto object the integrator/app passes in, and fallback behaviour when subtle is unavailable
- Exploit idea: Submit '0x' and an odd-length hex string.
- Invariant to test: Hex inputs must be length-validated before signing.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: feed '0x' and odd-length values to resolveCrypto: digest and randomUUID defaults from globalThis.crypto and assert rejection.
