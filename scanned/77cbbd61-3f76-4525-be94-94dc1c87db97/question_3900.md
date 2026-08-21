# Q3900: ping doubles as a liveness oracle in encodings.ts

## Question
ping() invokes privy:iframe:ready with a caller-controlled timeout; can an attacker use base64 / utf8 conversions used for signing payloads and signatures to keep the ready state true while the iframe is actually serving a different session?

## Target
- File/function: [src/utils/encodings.ts](src/utils/encodings.ts) - base64 / utf8 conversions used for signing payloads and signatures
- Entrypoint: generateAuthorizationSignature and every proxy signMessage payload
- Attacker controls: byte strings crossing the encode/decode boundary
- Exploit idea: Flip the iframe session and observe the cached ready flag.
- Invariant to test: Readiness must be invalidated when the underlying wallet session changes.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: change the session and assert base64 / utf8 conversions used for signing payloads and signatures re-verifies readiness.
