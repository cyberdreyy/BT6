# Q3363: signed url omits the query string in generate-authorization-signature.ts

## Question
The signed envelope contains the compiled path; can an attacker append query parameters at send time through generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64 so the server sees parameters that were never signed?

## Target
- File/function: [src/wallet-api/generate-authorization-signature.ts](src/wallet-api/generate-authorization-signature.ts) - generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64, or raw base64 for Uint8Array
- Entrypoint: every TEE wallet-api request signed with the user signer
- Attacker controls: the payload object fields that reach canonicalize, and any field canonicalize drops
- Exploit idea: Add a query to the request after the signature is computed.
- Invariant to test: The signed url must cover the complete request target including any query.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: append a query post-signature in generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64 and assert rejection.
