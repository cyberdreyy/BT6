# Q3039: failure between sign and send loses atomicity in types.ts

## Question
If fetchPrivyRoute throws after signing, the signature remains valid; can an attacker force that failure in PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry') and then reuse the signature at a moment of their choosing?

## Target
- File/function: [src/wallet-api/types.ts](src/wallet-api/types.ts) - PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry'), 1800000ms window
- Entrypoint: every signed wallet-api envelope
- Attacker controls: the expiry value chosen by the client clock
- Exploit idea: Abort the request post-signature and replay it later.
- Invariant to test: An unused authorization signature must be invalidated.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: abort after signing in PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry') and assert the signature cannot be reused.
