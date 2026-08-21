# Q1603: access token captured in the signing closure in generate-authorization-signature.ts

## Question
The signer closure captures the access token at construction; can an attacker keep a stale closure alive so generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64 signs using a token belonging to a previous session?

## Target
- File/function: [src/wallet-api/generate-authorization-signature.ts](src/wallet-api/generate-authorization-signature.ts) - generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64, or raw base64 for Uint8Array
- Entrypoint: every TEE wallet-api request signed with the user signer
- Attacker controls: the payload object fields that reach canonicalize, and any field canonicalize drops
- Exploit idea: Obtain the closure, change sessions, then sign.
- Invariant to test: Signing must resolve the current session token at call time.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: change sessions and assert generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64 refuses to reuse the captured token.
