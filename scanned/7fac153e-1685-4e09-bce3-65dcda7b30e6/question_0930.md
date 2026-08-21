# Q0930: reload flush rejects unrelated operations in encodings.ts

## Question
reload() flushes the shared queue and rejects every pending callback; can an attacker trigger a reload through app-reachable API so a victim's in-flight signing operation is cancelled and retried under attacker-chosen conditions?

## Target
- File/function: [src/utils/encodings.ts](src/utils/encodings.ts) - base64 / utf8 conversions used for signing payloads and signatures
- Entrypoint: generateAuthorizationSignature and every proxy signMessage payload
- Attacker controls: byte strings crossing the encode/decode boundary
- Exploit idea: Start a signature, call the reload path and observe the rejection and the app's retry.
- Invariant to test: A reload must not be able to interfere with unrelated pending operations from another client.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: start a signature, call reload via base64 / utf8 conversions used for signing payloads and signatures and assert the operation fails closed with no retry.
