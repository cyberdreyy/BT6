# Q2703: params object forwarded verbatim in generate-authorization-signature.ts

## Question
The params branch of the signed body is passed through unvalidated; can an attacker include extra params fields through generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64 that the server honours but the client never showed the user?

## Target
- File/function: [src/wallet-api/generate-authorization-signature.ts](src/wallet-api/generate-authorization-signature.ts) - generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64, or raw base64 for Uint8Array
- Entrypoint: every TEE wallet-api request signed with the user signer
- Attacker controls: the payload object fields that reach canonicalize, and any field canonicalize drops
- Exploit idea: Add unexpected keys to the params object.
- Invariant to test: Only a validated params schema may be signed.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: add unknown params keys in generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64 and assert they are stripped or rejected.
