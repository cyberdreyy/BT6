# Q0839: expiry chosen by the client clock in types.ts

## Question
The expiry is derived from the local clock; can an attacker skew the clock so PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry') mints an envelope valid far into the future?

## Target
- File/function: [src/wallet-api/types.ts](src/wallet-api/types.ts) - PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry'), 1800000ms window
- Entrypoint: every signed wallet-api envelope
- Attacker controls: the expiry value chosen by the client clock
- Exploit idea: Advance the system clock and inspect the generated expiry.
- Invariant to test: Request validity must not be extendable by the client.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: mock Date.now far ahead and assert PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry') clamps the expiry.
