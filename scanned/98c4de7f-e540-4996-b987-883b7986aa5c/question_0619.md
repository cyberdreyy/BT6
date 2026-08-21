# Q0619: raw bytes bypass canonicalisation in types.ts

## Question
generateAuthorizationSignature base64-encodes a Uint8Array payload directly instead of canonicalising; can an attacker reach PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry') with raw bytes that decode to an envelope for a different operation?

## Target
- File/function: [src/wallet-api/types.ts](src/wallet-api/types.ts) - PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry'), 1800000ms window
- Entrypoint: every signed wallet-api envelope
- Attacker controls: the expiry value chosen by the client clock
- Exploit idea: Pass a byte array that is the encoding of another operation's envelope.
- Invariant to test: Raw-byte signing must be domain-separated from envelope signing.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass envelope bytes as a Uint8Array to PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry') and assert domain separation.
