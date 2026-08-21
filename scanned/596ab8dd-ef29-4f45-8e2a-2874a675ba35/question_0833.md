# Q0833: expiry chosen by the client clock in generate-authorization-signature.ts

## Question
The expiry is derived from the local clock; can an attacker skew the clock so generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64 mints an envelope valid far into the future?

## Target
- File/function: [src/wallet-api/generate-authorization-signature.ts](src/wallet-api/generate-authorization-signature.ts) - generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64, or raw base64 for Uint8Array
- Entrypoint: every TEE wallet-api request signed with the user signer
- Attacker controls: the payload object fields that reach canonicalize, and any field canonicalize drops
- Exploit idea: Advance the system clock and inspect the generated expiry.
- Invariant to test: Request validity must not be extendable by the client.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: mock Date.now far ahead and assert generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64 clamps the expiry.
