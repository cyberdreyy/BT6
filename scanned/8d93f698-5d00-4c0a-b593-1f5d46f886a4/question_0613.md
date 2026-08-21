# Q0613: raw bytes bypass canonicalisation in generate-authorization-signature.ts

## Question
generateAuthorizationSignature base64-encodes a Uint8Array payload directly instead of canonicalising; can an attacker reach generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64 with raw bytes that decode to an envelope for a different operation?

## Target
- File/function: [src/wallet-api/generate-authorization-signature.ts](src/wallet-api/generate-authorization-signature.ts) - generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64, or raw base64 for Uint8Array
- Entrypoint: every TEE wallet-api request signed with the user signer
- Attacker controls: the payload object fields that reach canonicalize, and any field canonicalize drops
- Exploit idea: Pass a byte array that is the encoding of another operation's envelope.
- Invariant to test: Raw-byte signing must be domain-separated from envelope signing.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass envelope bytes as a Uint8Array to generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64 and assert domain separation.
