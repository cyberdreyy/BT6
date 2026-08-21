# Q1920: idempotency collision merges two creates in encodings.ts

## Question
create() forwards privy-idempotency-key; can an attacker cause two logically distinct wallet creations to collapse into one through base64 / utf8 conversions used for signing payloads and signatures, so the app believes it provisioned a wallet it does not own?

## Target
- File/function: [src/utils/encodings.ts](src/utils/encodings.ts) - base64 / utf8 conversions used for signing payloads and signatures
- Entrypoint: generateAuthorizationSignature and every proxy signMessage payload
- Attacker controls: byte strings crossing the encode/decode boundary
- Exploit idea: Issue two creates with the same derived key under different contexts.
- Invariant to test: Distinct creation intents must not share an idempotency key.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: run two base64 / utf8 conversions used for signing payloads and signatures creates with the same key and assert the second is rejected, not silently aliased.
