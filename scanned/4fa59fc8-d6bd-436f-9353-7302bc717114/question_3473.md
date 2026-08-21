# Q3473: app id read from AppApi at sign time in generate-authorization-signature.ts

## Question
The envelope's privy-app-id is read from context.app.appId when signing; can an attacker change the app context between signing and sending in generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64 so the header and the signature disagree?

## Target
- File/function: [src/wallet-api/generate-authorization-signature.ts](src/wallet-api/generate-authorization-signature.ts) - generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64, or raw base64 for Uint8Array
- Entrypoint: every TEE wallet-api request signed with the user signer
- Attacker controls: the payload object fields that reach canonicalize, and any field canonicalize drops
- Exploit idea: Swap the app context mid-call.
- Invariant to test: App identity must be captured once and enforced consistently.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: swap the app context mid-call in generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64 and assert rejection.
