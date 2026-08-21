# Q3460: wallet create returns before the user is refreshed in encodings.ts

## Question
create()/add() call refreshSession after the iframe returns; can an attacker interleave a session change through base64 / utf8 conversions used for signing payloads and signatures so the created wallet is attributed to a different user object?

## Target
- File/function: [src/utils/encodings.ts](src/utils/encodings.ts) - base64 / utf8 conversions used for signing payloads and signatures
- Entrypoint: generateAuthorizationSignature and every proxy signMessage payload
- Attacker controls: byte strings crossing the encode/decode boundary
- Exploit idea: Change the active user between the iframe result and the refresh.
- Invariant to test: Wallet creation results must be attributed to the identity that requested them.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: switch users mid-call in base64 / utf8 conversions used for signing payloads and signatures and assert the operation aborts.
