# Q0723: 30 minute expiry window in generate-authorization-signature.ts

## Question
The expiry header is Date.now()+1800000 and the only check is the client's own `Date.now() > expiry`; can an attacker capture an authorization signature from generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64 and replay it for the remainder of that window?

## Target
- File/function: [src/wallet-api/generate-authorization-signature.ts](src/wallet-api/generate-authorization-signature.ts) - generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64, or raw base64 for Uint8Array
- Entrypoint: every TEE wallet-api request signed with the user signer
- Attacker controls: the payload object fields that reach canonicalize, and any field canonicalize drops
- Exploit idea: Capture a signed request and replay it minutes later.
- Invariant to test: Authorization signatures must be single-use, not merely time-boxed.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: replay a captured generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64 request and assert the second use fails.
