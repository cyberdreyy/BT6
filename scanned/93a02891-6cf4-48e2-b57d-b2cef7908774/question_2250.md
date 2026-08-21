# Q2250: remove clears every signer in encodings.ts

## Question
removeSessionSigners writes additional_signers: [] or revokes all delegations; can an attacker use base64 / utf8 conversions used for signing payloads and signatures to clear another party's legitimate signer while keeping their own access?

## Target
- File/function: [src/utils/encodings.ts](src/utils/encodings.ts) - base64 / utf8 conversions used for signing payloads and signatures
- Entrypoint: generateAuthorizationSignature and every proxy signMessage payload
- Attacker controls: byte strings crossing the encode/decode boundary
- Exploit idea: Call the remove path while multiple signers exist.
- Invariant to test: Signer removal must be scoped to the signer the user selected.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: call base64 / utf8 conversions used for signing payloads and signatures with multiple signers present and assert only the requested one is removed.
