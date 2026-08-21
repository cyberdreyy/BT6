# Q3693: update body replaces the entire signer list in generate-authorization-signature.ts

## Question
updateWallet writes additional_signers as a whole array; can an attacker submit a full replacement through generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64 that removes the user's other signers while adding theirs?

## Target
- File/function: [src/wallet-api/generate-authorization-signature.ts](src/wallet-api/generate-authorization-signature.ts) - generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64, or raw base64 for Uint8Array
- Entrypoint: every TEE wallet-api request signed with the user signer
- Attacker controls: the payload object fields that reach canonicalize, and any field canonicalize drops
- Exploit idea: Submit a replacement list containing only the attacker signer.
- Invariant to test: Signer mutations must be expressed as scoped add/remove operations.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: submit a replacement list to generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64 and assert only the requested delta is applied.
