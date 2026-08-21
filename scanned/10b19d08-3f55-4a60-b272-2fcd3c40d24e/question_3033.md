# Q3033: failure between sign and send loses atomicity in generate-authorization-signature.ts

## Question
If fetchPrivyRoute throws after signing, the signature remains valid; can an attacker force that failure in generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64 and then reuse the signature at a moment of their choosing?

## Target
- File/function: [src/wallet-api/generate-authorization-signature.ts](src/wallet-api/generate-authorization-signature.ts) - generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64, or raw base64 for Uint8Array
- Entrypoint: every TEE wallet-api request signed with the user signer
- Attacker controls: the payload object fields that reach canonicalize, and any field canonicalize drops
- Exploit idea: Abort the request post-signature and replay it later.
- Invariant to test: An unused authorization signature must be invalidated.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: abort after signing in generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64 and assert the signature cannot be reused.
