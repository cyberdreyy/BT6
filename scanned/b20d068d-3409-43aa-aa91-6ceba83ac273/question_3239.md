# Q3239: digest injected through constructor options in resolve.ts

## Question
Privy accepts a crypto option that supplies digest; can an attacker pass an implementation through resolveCrypto: digest and randomUUID defaults from globalThis.crypto that returns a fixed challenge so PKCE binding is defeated?

## Target
- File/function: [src/crypto/resolve.ts](src/crypto/resolve.ts) - resolveCrypto: digest and randomUUID defaults from globalThis.crypto
- Entrypoint: new Privy({crypto})
- Attacker controls: the crypto object the integrator/app passes in, and fallback behaviour when subtle is unavailable
- Exploit idea: Construct the client with a crypto object returning constant digests.
- Invariant to test: A caller-supplied crypto implementation must not weaken PKCE or key derivation.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: pass a constant-digest crypto to resolveCrypto: digest and randomUUID defaults from globalThis.crypto and assert the flow refuses or the challenge stays unique.
