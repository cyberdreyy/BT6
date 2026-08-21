# Q0173: body signed separately from the sent body in generate-authorization-signature.ts

## Question
The signature covers `{...request}` while fetchPrivyRoute is called with the same object by reference; can an attacker mutate the request object between signing and sending so generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64 transmits a body the signature does not cover?

## Target
- File/function: [src/wallet-api/generate-authorization-signature.ts](src/wallet-api/generate-authorization-signature.ts) - generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64, or raw base64 for Uint8Array
- Entrypoint: every TEE wallet-api request signed with the user signer
- Attacker controls: the payload object fields that reach canonicalize, and any field canonicalize drops
- Exploit idea: Pass an object with a mutating getter or mutate it from a microtask between the two awaits.
- Invariant to test: The signed bytes and the transmitted bytes must be the same immutable snapshot.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: mutate the body between sign and send in generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64 and assert the request is rejected.
