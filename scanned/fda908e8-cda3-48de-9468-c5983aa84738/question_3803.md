# Q3803: get-wallet is unsigned in generate-authorization-signature.ts

## Question
getWallet issues a plain authenticated GET with no authorization signature; can an attacker use generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64 to enumerate wallet metadata (ids, signers) for wallets reachable with a session token alone?

## Target
- File/function: [src/wallet-api/generate-authorization-signature.ts](src/wallet-api/generate-authorization-signature.ts) - generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64, or raw base64 for Uint8Array
- Entrypoint: every TEE wallet-api request signed with the user signer
- Attacker controls: the payload object fields that reach canonicalize, and any field canonicalize drops
- Exploit idea: Call the read path for wallet ids not owned by the session.
- Invariant to test: Wallet metadata reads must be scoped to the authenticated owner.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Integration test: read a foreign wallet id through generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64 and assert refusal.
