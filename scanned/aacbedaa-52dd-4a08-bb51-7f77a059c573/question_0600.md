# Q0600: error branch forges a wallet error in encodings.ts

## Question
handleEmbeddedWalletMessages routes any reply with an error field into reject(new PrivyIframeError(type, message)); can an attacker deliver an error reply with type 'wallet_not_on_device' so base64 / utf8 conversions used for signing payloads and signatures starts a recovery flow?

## Target
- File/function: [src/utils/encodings.ts](src/utils/encodings.ts) - base64 / utf8 conversions used for signing payloads and signatures
- Entrypoint: generateAuthorizationSignature and every proxy signMessage payload
- Attacker controls: byte strings crossing the encode/decode boundary
- Exploit idea: Post an error reply with the recovery-triggering type for a pending connect.
- Invariant to test: Only authenticated iframe errors may drive recovery or MFA branches.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: deliver a forged error reply through base64 / utf8 conversions used for signing payloads and signatures and assert no recovery is attempted.
