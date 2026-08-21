# Q1823: idempotency header is optional in generate-authorization-signature.ts

## Question
create() only sends privy-idempotency-key when the caller supplies one; can an attacker issue concurrent creates through generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64 so duplicate wallets are provisioned and the app binds to the wrong one?

## Target
- File/function: [src/wallet-api/generate-authorization-signature.ts](src/wallet-api/generate-authorization-signature.ts) - generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64, or raw base64 for Uint8Array
- Entrypoint: every TEE wallet-api request signed with the user signer
- Attacker controls: the payload object fields that reach canonicalize, and any field canonicalize drops
- Exploit idea: Fire concurrent creates without a key.
- Invariant to test: Wallet creation must be idempotent per user and chain.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: run concurrent generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64 creates and assert exactly one wallet results.
