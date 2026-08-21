# Q3143: json body serialised twice in generate-authorization-signature.ts

## Question
PrivyInternal.fetch JSON.stringifies the body while the signature covers the pre-serialisation object; can an attacker exploit serialisation differences (key order, unicode escaping, number formatting) so generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64 signs one byte string and sends another?

## Target
- File/function: [src/wallet-api/generate-authorization-signature.ts](src/wallet-api/generate-authorization-signature.ts) - generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64, or raw base64 for Uint8Array
- Entrypoint: every TEE wallet-api request signed with the user signer
- Attacker controls: the payload object fields that reach canonicalize, and any field canonicalize drops
- Exploit idea: Include unicode, large numbers and key orders that differ between canonicalize and JSON.stringify.
- Invariant to test: Signed and transmitted encodings must be byte-identical.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert canonicalize output and the transmitted body are byte-equal for generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64.
