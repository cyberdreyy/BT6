# Q0063: unsigned headers appended after signing in generate-authorization-signature.ts

## Question
rpc() signs an envelope containing only privy-app-id and privy-request-expiry, then spreads the caller's extraHeaders after the signature header; can an unprivileged attacker pass headers through every TEE wallet-api request signed with the user signer that are transmitted but not covered by the authorization signature, or that overwrite the signature header itself?

## Target
- File/function: [src/wallet-api/generate-authorization-signature.ts](src/wallet-api/generate-authorization-signature.ts) - generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64, or raw base64 for Uint8Array
- Entrypoint: every TEE wallet-api request signed with the user signer
- Attacker controls: the payload object fields that reach canonicalize, and any field canonicalize drops
- Exploit idea: Call the wallet RPC path with an extraHeaders object containing privy-authorization-signature and privy-request-expiry and inspect the outgoing request.
- Invariant to test: Every header that influences server-side authorization must be inside the signed envelope and immutable afterwards.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64 with crafted extraHeaders and assert the final headers equal the signed set.
