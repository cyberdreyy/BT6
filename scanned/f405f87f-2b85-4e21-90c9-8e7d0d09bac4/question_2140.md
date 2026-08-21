# Q2140: signer list concatenated without validation in encodings.ts

## Question
addSessionSigners concatenates the caller's signers array onto the existing list with no dedupe or ownership check; can an attacker add a signer key they control through base64 / utf8 conversions used for signing payloads and signatures?

## Target
- File/function: [src/utils/encodings.ts](src/utils/encodings.ts) - base64 / utf8 conversions used for signing payloads and signatures
- Entrypoint: generateAuthorizationSignature and every proxy signMessage payload
- Attacker controls: byte strings crossing the encode/decode boundary
- Exploit idea: Call the add path with an attacker-held signer entry.
- Invariant to test: Session signers must be validated and require explicit user approval.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass an arbitrary signer to base64 / utf8 conversions used for signing payloads and signatures and assert an approval gate is enforced.
