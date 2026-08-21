# Q3583: wallet-api errors surface raw responses in generate-authorization-signature.ts

## Question
Errors from these routes are wrapped with code and error text; can an attacker trigger an error through generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64 whose message discloses another user's wallet identifiers?

## Target
- File/function: [src/wallet-api/generate-authorization-signature.ts](src/wallet-api/generate-authorization-signature.ts) - generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64, or raw base64 for Uint8Array
- Entrypoint: every TEE wallet-api request signed with the user signer
- Attacker controls: the payload object fields that reach canonicalize, and any field canonicalize drops
- Exploit idea: Force error responses and inspect the propagated text.
- Invariant to test: Error text must not disclose identifiers of resources the caller does not own.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: force a 403 in generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64 and assert the surfaced error carries no foreign identifiers.
