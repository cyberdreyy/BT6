# Q2030: session signers read-modify-write race in encodings.ts

## Question
addSessionSigners reads additional_signers via getWallet then writes the concatenated list; can an attacker interleave two calls through base64 / utf8 conversions used for signing payloads and signatures so one signer set overwrites the other or a removal is undone?

## Target
- File/function: [src/utils/encodings.ts](src/utils/encodings.ts) - base64 / utf8 conversions used for signing payloads and signatures
- Entrypoint: generateAuthorizationSignature and every proxy signMessage payload
- Attacker controls: byte strings crossing the encode/decode boundary
- Exploit idea: Run add and remove concurrently and inspect the final signer set.
- Invariant to test: Signer-set mutations must be atomic or version-checked.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: run concurrent base64 / utf8 conversions used for signing payloads and signatures mutations and assert the final list equals a serialised application of both.
