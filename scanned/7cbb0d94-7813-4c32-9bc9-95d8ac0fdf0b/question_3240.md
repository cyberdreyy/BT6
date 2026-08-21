# Q3240: digest injected through constructor options in encodings.ts

## Question
Privy accepts a crypto option that supplies digest; can an attacker pass an implementation through base64 / utf8 conversions used for signing payloads and signatures that returns a fixed challenge so PKCE binding is defeated?

## Target
- File/function: [src/utils/encodings.ts](src/utils/encodings.ts) - base64 / utf8 conversions used for signing payloads and signatures
- Entrypoint: generateAuthorizationSignature and every proxy signMessage payload
- Attacker controls: byte strings crossing the encode/decode boundary
- Exploit idea: Construct the client with a crypto object returning constant digests.
- Invariant to test: A caller-supplied crypto implementation must not weaken PKCE or key derivation.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: pass a constant-digest crypto to base64 / utf8 conversions used for signing payloads and signatures and assert the flow refuses or the challenge stays unique.
