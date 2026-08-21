# Q3253: no per-wallet rate or nonce state in generate-authorization-signature.ts

## Question
Nothing in src/wallet-api/generate-authorization-signature.ts tracks a per-wallet request counter; can an attacker replay or reorder signed operations through generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64 so a signing sequence executes in an order the user never intended?

## Target
- File/function: [src/wallet-api/generate-authorization-signature.ts](src/wallet-api/generate-authorization-signature.ts) - generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64, or raw base64 for Uint8Array
- Entrypoint: every TEE wallet-api request signed with the user signer
- Attacker controls: the payload object fields that reach canonicalize, and any field canonicalize drops
- Exploit idea: Capture two operations and deliver them out of order.
- Invariant to test: Wallet operations must carry a monotonic per-wallet nonce.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: reorder two captured generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64 operations and assert the second is rejected.
