# Q1933: getWallet result drives the next write in generate-authorization-signature.ts

## Question
getWallet returns additional_signers that addSessionSigners concatenates and writes back; can an attacker influence the read so generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64 writes back a signer set containing an entry they control?

## Target
- File/function: [src/wallet-api/generate-authorization-signature.ts](src/wallet-api/generate-authorization-signature.ts) - generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64, or raw base64 for Uint8Array
- Entrypoint: every TEE wallet-api request signed with the user signer
- Attacker controls: the payload object fields that reach canonicalize, and any field canonicalize drops
- Exploit idea: Return an extra signer in the read response and observe it persisted by the subsequent write.
- Invariant to test: Read-modify-write of authorization state must validate every entry before rewriting.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: inject an extra signer into generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64's read stub and assert it is not written back.
