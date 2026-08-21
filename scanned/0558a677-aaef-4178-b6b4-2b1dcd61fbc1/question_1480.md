# Q1480: entropyId is just the wallet address in encodings.ts

## Question
getEntropyDetailsFromAccount uses the account address as the entropyId; can an attacker pass an address they merely know through base64 / utf8 conversions used for signing payloads and signatures and cause the iframe to load or recover the wrong wallet?

## Target
- File/function: [src/utils/encodings.ts](src/utils/encodings.ts) - base64 / utf8 conversions used for signing payloads and signatures
- Entrypoint: generateAuthorizationSignature and every proxy signMessage payload
- Attacker controls: byte strings crossing the encode/decode boundary
- Exploit idea: Call the provider path with a foreign address as entropyId.
- Invariant to test: Entropy identifiers must be validated against the authenticated user's own accounts.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a foreign address into base64 / utf8 conversions used for signing payloads and signatures and assert it is rejected before the proxy call.
