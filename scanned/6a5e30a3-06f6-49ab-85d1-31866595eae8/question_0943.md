# Q0943: expiry check is a tautology in generate-authorization-signature.ts

## Question
The guard compares Date.now() against a value just computed from Date.now(); can an attacker rely on this dead check so generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64 never actually rejects a stale envelope?

## Target
- File/function: [src/wallet-api/generate-authorization-signature.ts](src/wallet-api/generate-authorization-signature.ts) - generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64, or raw base64 for Uint8Array
- Entrypoint: every TEE wallet-api request signed with the user signer
- Attacker controls: the payload object fields that reach canonicalize, and any field canonicalize drops
- Exploit idea: Trace the branch and confirm it can only trigger under an implausible delay.
- Invariant to test: Freshness must be validated against the moment of transmission.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: delay between construction and send in generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64 and assert the stale envelope is rejected.
