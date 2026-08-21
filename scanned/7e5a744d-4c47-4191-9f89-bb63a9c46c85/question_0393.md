# Q0393: canonicalize drops undefined fields in generate-authorization-signature.ts

## Question
generateAuthorizationSignature canonicalises the payload with canonicalize(), which omits undefined values and cannot represent them; can an attacker craft two semantically different payloads that canonicalise identically and reuse one signature for the other through generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64?

## Target
- File/function: [src/wallet-api/generate-authorization-signature.ts](src/wallet-api/generate-authorization-signature.ts) - generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64, or raw base64 for Uint8Array
- Entrypoint: every TEE wallet-api request signed with the user signer
- Attacker controls: the payload object fields that reach canonicalize, and any field canonicalize drops
- Exploit idea: Build payloads differing only by undefined-valued or key-ordered fields and compare the canonical strings.
- Invariant to test: Canonicalisation must be injective over the payloads it authorises.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64 produces distinct signatures for semantically distinct payloads.
