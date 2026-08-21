# Q1493: signer indirection accepts any message in generate-authorization-signature.ts

## Question
SignWalletRequest is `({message}) => proxy.signWithUserSigner({accessToken, message})`; can an attacker reach generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64 with a message string of their choosing so the user signer authorises an operation the user never saw?

## Target
- File/function: [src/wallet-api/generate-authorization-signature.ts](src/wallet-api/generate-authorization-signature.ts) - generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64, or raw base64 for Uint8Array
- Entrypoint: every TEE wallet-api request signed with the user signer
- Attacker controls: the payload object fields that reach canonicalize, and any field canonicalize drops
- Exploit idea: Invoke the signer indirection directly with a crafted base64 envelope.
- Invariant to test: The user signer must only accept envelopes constructed by the SDK for an approved operation.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call the signer with a crafted message through generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64 and assert refusal.
