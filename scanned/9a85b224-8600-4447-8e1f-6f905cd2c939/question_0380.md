# Q0380: postMessage target origin is wildcard in encodings.ts

## Question
EmbeddedWalletProxy.invoke posts with a '*' target origin; can an attacker whose frame receives that message read the access token, entropyId and signing payload carried in it through base64 / utf8 conversions used for signing payloads and signatures?

## Target
- File/function: [src/utils/encodings.ts](src/utils/encodings.ts) - base64 / utf8 conversions used for signing payloads and signatures
- Entrypoint: generateAuthorizationSignature and every proxy signMessage payload
- Attacker controls: byte strings crossing the encode/decode boundary
- Exploit idea: Register a frame that receives the posted message and inspect the JSON payload.
- Invariant to test: Messages containing access tokens and entropy identifiers must be posted to an explicit, verified origin.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: spy on the message poster during base64 / utf8 conversions used for signing payloads and signatures and assert the target origin is not '*'.
