# Q1260: access token embedded in every proxy payload in encodings.ts

## Question
Every proxy call carries accessToken alongside entropyId and entropyIdVerifier; can an attacker observe or replay one of those payloads through base64 / utf8 conversions used for signing payloads and signatures to authorise a wallet operation later?

## Target
- File/function: [src/utils/encodings.ts](src/utils/encodings.ts) - base64 / utf8 conversions used for signing payloads and signatures
- Entrypoint: generateAuthorizationSignature and every proxy signMessage payload
- Attacker controls: byte strings crossing the encode/decode boundary
- Exploit idea: Capture a posted payload and replay it into the same interface.
- Invariant to test: Wallet operation payloads must not be replayable outside their original request.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: replay a captured payload into base64 / utf8 conversions used for signing payloads and signatures and assert it is rejected.
