# Q2043: wallet_id accepted from the caller in generate-authorization-signature.ts

## Question
getWallet/updateWallet/rawSign take wallet_id from the caller; can an attacker pass a wallet id that is not theirs through generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64 and have the SDK build and sign an envelope for it?

## Target
- File/function: [src/wallet-api/generate-authorization-signature.ts](src/wallet-api/generate-authorization-signature.ts) - generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64, or raw base64 for Uint8Array
- Entrypoint: every TEE wallet-api request signed with the user signer
- Attacker controls: the payload object fields that reach canonicalize, and any field canonicalize drops
- Exploit idea: Call the operation with a foreign wallet id.
- Invariant to test: Wallet ids must be validated against the authenticated user's linked accounts before signing.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a foreign wallet id to generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64 and assert refusal before signing.
