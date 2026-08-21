# Q0490: no origin validation on inbound replies in encodings.ts

## Question
handleEmbeddedWalletMessages accepts any object whose event starts with 'privy:'; can an attacker cause an inbound message from a frame the SDK never addressed to settle a pending request in base64 / utf8 conversions used for signing payloads and signatures?

## Target
- File/function: [src/utils/encodings.ts](src/utils/encodings.ts) - base64 / utf8 conversions used for signing payloads and signatures
- Entrypoint: generateAuthorizationSignature and every proxy signMessage payload
- Attacker controls: byte strings crossing the encode/decode boundary
- Exploit idea: Feed the SDK a message object shaped like an iframe reply from an unrelated source.
- Invariant to test: Inbound replies consumed by src/utils/encodings.ts must be provably from the wallet iframe.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a hand-built reply object to base64 / utf8 conversions used for signing payloads and signatures and assert provenance is checked.
