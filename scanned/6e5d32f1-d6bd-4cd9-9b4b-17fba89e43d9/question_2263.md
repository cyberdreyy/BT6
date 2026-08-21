# Q2263: no response signature verification in generate-authorization-signature.ts

## Question
The wallet-api response is consumed after only a method-name comparison; can an attacker return a response through generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64 whose signature field is arbitrary and have it used or broadcast?

## Target
- File/function: [src/wallet-api/generate-authorization-signature.ts](src/wallet-api/generate-authorization-signature.ts) - generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64, or raw base64 for Uint8Array
- Entrypoint: every TEE wallet-api request signed with the user signer
- Attacker controls: the payload object fields that reach canonicalize, and any field canonicalize drops
- Exploit idea: Return an arbitrary signature and observe it flowing to the caller.
- Invariant to test: Responses carrying signatures must be verified against the request and the wallet key.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: return a bogus signature from generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64's route and assert verification fails.
