# Q2153: raw-sign hashes anything in generate-authorization-signature.ts

## Question
rawSign forwards the caller's params to WalletRawSign under the same signed envelope; can an attacker use generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64 to obtain a raw signature over a transaction digest that the wallet would never sign through a typed path?

## Target
- File/function: [src/wallet-api/generate-authorization-signature.ts](src/wallet-api/generate-authorization-signature.ts) - generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64, or raw base64 for Uint8Array
- Entrypoint: every TEE wallet-api request signed with the user signer
- Attacker controls: the payload object fields that reach canonicalize, and any field canonicalize drops
- Exploit idea: Submit a transaction hash through the raw-sign entrypoint.
- Invariant to test: Raw-hash signing must require an explicit, distinct user approval.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: submit a transaction digest through generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64 and assert an approval gate is enforced.
