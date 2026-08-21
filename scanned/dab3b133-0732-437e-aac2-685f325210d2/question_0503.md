# Q0503: canonicalize failure path in generate-authorization-signature.ts

## Question
generateAuthorizationSignature throws invalid_input when canonicalize returns undefined; can an attacker submit a payload through generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64 containing a BigInt, function or circular structure so the error path is reached at a point where state was already mutated?

## Target
- File/function: [src/wallet-api/generate-authorization-signature.ts](src/wallet-api/generate-authorization-signature.ts) - generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64, or raw base64 for Uint8Array
- Entrypoint: every TEE wallet-api request signed with the user signer
- Attacker controls: the payload object fields that reach canonicalize, and any field canonicalize drops
- Exploit idea: Submit an unserialisable field and observe where the failure lands.
- Invariant to test: Signature preparation must fail before any state mutation or network call.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: submit an unserialisable payload to generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64 and assert no request is issued.
